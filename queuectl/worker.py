import os
import signal
import subprocess
import threading
import time

from queuectl import db, pidfile

POLL_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 5.0
HEARTBEAT_TIMEOUT = 15.0
REAP_INTERVAL = 5.0


def send_heartbeats(db_path, job_id, stop_event):
    conn = db.get_connection(db_path)
    while not stop_event.wait(HEARTBEAT_INTERVAL):
        db.heartbeat_job(conn, job_id)
    conn.close()


def execute_job(db_path, job):
    stop_event = threading.Event()
    hb_thread = threading.Thread(
        target=send_heartbeats, args=(db_path, job["id"], stop_event), daemon=True
    )
    hb_thread.start()

    # start_new_session=True keeps the job's process in its own group, so a
    # signal sent to the worker (Ctrl+C, worker stop) doesn't also kill the
    # job command that's currently running.
    proc = subprocess.run(
        job["command"],
        shell=True,
        capture_output=True,
        text=True,
        start_new_session=True,
    )

    stop_event.set()
    hb_thread.join(timeout=2)

    conn = db.get_connection(db_path)
    if proc.returncode == 0:
        db.mark_completed(conn, job["id"])
    else:
        error = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
        cfg = db.config_all(conn)
        db.mark_failed(
            conn, job["id"], job["attempts"], job["max_retries"],
            float(cfg["backoff-base"]), error,
        )
    conn.close()


def run_worker_loop(db_path=None):
    db.init_db(db_path)
    pid = os.getpid()
    pidfile.register(pid)

    stop_requested = False

    def handle_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    last_reap_time = 0

    try:
        while not stop_requested:
            conn = db.get_connection(db_path)
            now = time.time()
            if now - last_reap_time >= REAP_INTERVAL:
                db.reap_stale_jobs(conn, HEARTBEAT_TIMEOUT)
                last_reap_time = now
            job = db.claim_next_job(conn, pid)
            conn.close()

            if job is None:
                time.sleep(POLL_INTERVAL)
                continue

            execute_job(db_path, job)
    finally:
        pidfile.unregister(pid)