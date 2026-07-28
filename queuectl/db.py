import os
import sqlite3

from queuectl.utils import utcnow_iso, iso_plus_seconds, new_token

DEFAULT_DB_DIR = os.environ.get("QUEUECTL_HOME", os.path.expanduser("~/.queuectl"))
DB_PATH = os.path.join(DEFAULT_DB_DIR, "queue.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    command       TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_retries   INTEGER NOT NULL DEFAULT 3,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    next_run_at   TEXT NOT NULL,
    worker_pid    INTEGER,
    claim_token   TEXT,
    heartbeat_at  TEXT,
    last_error    TEXT,
    dlq_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_claimable ON jobs(state, next_run_at);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

STATES = ("pending", "processing", "completed", "failed", "dead")

CONFIG_DEFAULTS = {"max-retries": "3", "backoff-base": "2"}


def get_connection(db_path=None):
    path = db_path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db(db_path=None):
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    conn.close()


def config_get(conn, key):
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    return CONFIG_DEFAULTS.get(key)


def config_set(conn, key, value):
    conn.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def config_all(conn):
    out = dict(CONFIG_DEFAULTS)
    for row in conn.execute("SELECT key, value FROM config"):
        out[row["key"]] = row["value"]
    return out


def enqueue_job(conn, job_id, command, max_retries):
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO jobs
           (id, command, state, attempts, max_retries, created_at, updated_at, next_run_at)
           VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)""",
        (job_id, command, max_retries, now, now, now),
    )


def get_job(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def list_jobs(conn, state=None):
    if state:
        return conn.execute(
            "SELECT * FROM jobs WHERE state=? ORDER BY created_at ASC", (state,)
        ).fetchall()
    return conn.execute("SELECT * FROM jobs ORDER BY created_at ASC").fetchall()


def state_counts(conn):
    counts = {s: 0 for s in STATES}
    for row in conn.execute("SELECT state, COUNT(*) c FROM jobs GROUP BY state"):
        counts[row["state"]] = row["c"]
    return counts


# A worker calls this to grab the next job. The UPDATE picks the row and
# flips its state in one statement, so two workers can never both claim
# the same job - SQLite only lets one process write at a time, so the
# second worker's UPDATE always runs after the first one has already
# committed and changed the state.
def claim_next_job(conn, worker_pid):
    now = utcnow_iso()
    token = new_token()
    cur = conn.execute(
        """UPDATE jobs
           SET state='processing', worker_pid=?, claim_token=?,
               heartbeat_at=?, updated_at=?
           WHERE id = (
               SELECT id FROM jobs
               WHERE state IN ('pending', 'failed') AND next_run_at <= ?
               ORDER BY next_run_at ASC, created_at ASC
               LIMIT 1
           )""",
        (worker_pid, token, now, now, now),
    )
    if cur.rowcount == 0:
        return None
    return conn.execute("SELECT * FROM jobs WHERE claim_token=?", (token,)).fetchone()


def heartbeat_job(conn, job_id):
    conn.execute(
        "UPDATE jobs SET heartbeat_at=? WHERE id=? AND state='processing'",
        (utcnow_iso(), job_id),
    )


def mark_completed(conn, job_id):
    now = utcnow_iso()
    conn.execute(
        "UPDATE jobs SET state='completed', updated_at=?, heartbeat_at=NULL, "
        "worker_pid=NULL, claim_token=NULL WHERE id=?",
        (now, job_id),
    )


def mark_failed(conn, job_id, attempts, max_retries, backoff_base, error):
    now = utcnow_iso()
    attempts += 1
    error = (error or "")[:2000]

    if attempts >= max_retries:
        conn.execute(
            "UPDATE jobs SET state='dead', attempts=?, updated_at=?, dlq_at=?, "
            "last_error=?, heartbeat_at=NULL, worker_pid=NULL, claim_token=NULL WHERE id=?",
            (attempts, now, now, error, job_id),
        )
        return "dead"

    delay = backoff_base ** attempts
    next_run = iso_plus_seconds(now, delay)
    conn.execute(
        "UPDATE jobs SET state='failed', attempts=?, updated_at=?, next_run_at=?, "
        "last_error=?, heartbeat_at=NULL, worker_pid=NULL, claim_token=NULL WHERE id=?",
        (attempts, now, next_run, error, job_id),
    )
    return "failed"


def dlq_retry(conn, job_id):
    row = get_job(conn, job_id)
    if row is None or row["state"] != "dead":
        return False
    now = utcnow_iso()
    conn.execute(
        "UPDATE jobs SET state='pending', attempts=0, updated_at=?, next_run_at=?, "
        "dlq_at=NULL, last_error=NULL WHERE id=?",
        (now, now, job_id),
    )
    return True


# If a worker dies mid-job (crash, kill -9), the job is left stuck in
# 'processing' with a heartbeat that stops updating. Any worker that's
# still alive checks for this every few seconds and puts the job back to
# 'pending' so someone else can pick it up. attempts is left unchanged -
# a crash is not the job's own fault, so it shouldn't use up its retries.
def reap_stale_jobs(conn, heartbeat_timeout_seconds):
    cutoff = iso_plus_seconds(utcnow_iso(), -heartbeat_timeout_seconds)
    rows = conn.execute(
        "SELECT id FROM jobs WHERE state='processing' AND "
        "(heartbeat_at IS NULL OR heartbeat_at < ?)",
        (cutoff,),
    ).fetchall()

    ids = [r["id"] for r in rows]
    if not ids:
        return ids

    now = utcnow_iso()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE jobs SET state='pending', worker_pid=NULL, claim_token=NULL, "
        f"heartbeat_at=NULL, updated_at=?, next_run_at=?, "
        f"last_error='recovered after worker crash' WHERE id IN ({placeholders})",
        (now, now, *ids),
    )
    return ids