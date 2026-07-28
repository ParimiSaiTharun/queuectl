import os
import signal
import time

from queuectl.db import DEFAULT_DB_DIR

WORKERS_DIR = os.path.join(DEFAULT_DB_DIR, "workers")


def _path(pid):
    return os.path.join(WORKERS_DIR, f"{pid}.pid")


def register(pid):
    os.makedirs(WORKERS_DIR, exist_ok=True)
    with open(_path(pid), "w") as f:
        f.write(str(pid))


def unregister(pid):
    try:
        os.remove(_path(pid))
    except FileNotFoundError:
        pass


def is_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def list_worker_pids():
    if not os.path.isdir(WORKERS_DIR):
        return []

    pids = []
    for name in os.listdir(WORKERS_DIR):
        if not name.endswith(".pid"):
            continue
        try:
            pid = int(name[:-4])
        except ValueError:
            continue

        if is_alive(pid):
            pids.append(pid)
        else:
            try:
                os.remove(os.path.join(WORKERS_DIR, name))
            except FileNotFoundError:
                pass

    return sorted(pids)


def stop_all(timeout=15.0):
    pids = list_worker_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(is_alive(p) for p in pids):
            break
        time.sleep(0.2)

    return pids