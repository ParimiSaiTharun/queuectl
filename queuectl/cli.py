import argparse
import json
import multiprocessing
import os
import signal
import sys

from queuectl import db, pidfile
from queuectl.utils import new_job_id
from queuectl.worker import run_worker_loop


def open_db():
    db.init_db()
    return db.get_connection()


def job_to_dict(row):
    return {k: row[k] for k in row.keys()}


def cmd_enqueue(args):
    try:
        payload = json.loads(args.job_json)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 1

    if "command" not in payload or not payload["command"]:
        print("error: job JSON must include a non-empty \"command\"", file=sys.stderr)
        return 1

    conn = open_db()
    job_id = payload.get("id") or new_job_id()
    if db.get_job(conn, job_id) is not None:
        print(f"error: job id '{job_id}' already exists", file=sys.stderr)
        return 1

    max_retries = int(payload.get("max_retries", db.config_get(conn, "max-retries")))
    db.enqueue_job(conn, job_id, payload["command"], max_retries)
    print(f"enqueued {job_id}")
    return 0


def cmd_worker_start(args):
    count = max(1, args.count)
    db.init_db()

    if count == 1:
        run_worker_loop()
        return 0

    workers = []

    def propagate_signal(signum, frame):
        for w in workers:
            if w.is_alive():
                try:
                    os.kill(w.pid, signum)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGTERM, propagate_signal)
    signal.signal(signal.SIGINT, propagate_signal)

    for _ in range(count):
        w = multiprocessing.Process(target=run_worker_loop)
        w.start()
        workers.append(w)

    print(f"started {count} workers: {[w.pid for w in workers]}")
    for w in workers:
        w.join()
    return 0


def cmd_worker_stop(args):
    pids = pidfile.stop_all()
    if not pids:
        print("no running workers found")
    else:
        print(f"stopped workers: {pids}")
    return 0


def cmd_status(args):
    conn = open_db()
    counts = db.state_counts(conn)
    workers = pidfile.list_worker_pids()

    print("Job states:")
    for state in db.STATES:
        print(f"  {state:<10} {counts.get(state, 0)}")
    print(f"Active workers: {len(workers)} {workers}")
    return 0


def cmd_list(args):
    conn = open_db()
    rows = db.list_jobs(conn, args.state)

    if args.json:
        print(json.dumps([job_to_dict(r) for r in rows]))
        return 0

    if not rows:
        print("(no jobs)")
    for r in rows:
        print(f"{r['id']}  {r['state']:<11} attempts={r['attempts']}/{r['max_retries']}  {r['command']}")
    return 0


def cmd_dlq_list(args):
    conn = open_db()
    rows = db.list_jobs(conn, "dead")

    if args.json:
        print(json.dumps([job_to_dict(r) for r in rows]))
        return 0

    if not rows:
        print("(dlq empty)")
    for r in rows:
        print(f"{r['id']}  attempts={r['attempts']}/{r['max_retries']}  last_error={r['last_error']!r}  {r['command']}")
    return 0


def cmd_dlq_retry(args):
    conn = open_db()
    ok = db.dlq_retry(conn, args.job_id)
    if not ok:
        print(f"error: job '{args.job_id}' not found in dlq", file=sys.stderr)
        return 1
    print(f"re-enqueued {args.job_id} (attempts reset to 0)")
    return 0


def cmd_config_set(args):
    conn = open_db()
    db.config_set(conn, args.key, args.value)
    print(f"config: {args.key} = {args.value}")
    return 0


def cmd_config_get(args):
    conn = open_db()
    if args.key:
        print(db.config_get(conn, args.key))
    else:
        for k, v in db.config_all(conn).items():
            print(f"{k} = {v}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="queuectl", description="Background job queue CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("enqueue", help="Add a new job")
    p.add_argument("job_json", help='Job as JSON, e.g. \'{"command":"echo hi"}\'')
    p.set_defaults(func=cmd_enqueue)

    p = sub.add_parser("worker", help="Worker management")
    wsub = p.add_subparsers(dest="worker_command", required=True)

    p2 = wsub.add_parser("start", help="Start workers in the foreground")
    p2.add_argument("--count", type=int, default=1)
    p2.set_defaults(func=cmd_worker_start)

    p2 = wsub.add_parser("stop", help="Stop all running workers")
    p2.set_defaults(func=cmd_worker_stop)

    p = sub.add_parser("status", help="Summary of job states and active workers")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list", help="List jobs")
    p.add_argument("--state", choices=db.STATES, default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("dlq", help="Dead letter queue")
    dsub = p.add_subparsers(dest="dlq_command", required=True)

    p2 = dsub.add_parser("list", help="View DLQ jobs")
    p2.add_argument("--json", action="store_true")
    p2.set_defaults(func=cmd_dlq_list)

    p2 = dsub.add_parser("retry", help="Retry a DLQ job")
    p2.add_argument("job_id")
    p2.set_defaults(func=cmd_dlq_retry)

    p = sub.add_parser("config", help="Manage configuration")
    csub = p.add_subparsers(dest="config_command", required=True)

    p2 = csub.add_parser("set", help="Set a config key")
    p2.add_argument("key", choices=["max-retries", "backoff-base"])
    p2.add_argument("value")
    p2.set_defaults(func=cmd_config_set)

    p2 = csub.add_parser("get", help="Get config key(s)")
    p2.add_argument("key", nargs="?", default=None)
    p2.set_defaults(func=cmd_config_get)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()