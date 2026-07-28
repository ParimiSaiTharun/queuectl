import uuid
from datetime import datetime, timedelta, timezone


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_plus_seconds(iso_ts, seconds):
    dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    dt = dt + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_job_id():
    return uuid.uuid4().hex[:12]


def new_token():
    return uuid.uuid4().hex