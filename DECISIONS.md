# DECISIONS.md

## 1. Which exact line(s) prevent two workers from claiming the same job, and why is that atomic across processes?

This is in `queuectl/db.py`, in `claim_next_job`:

```python
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
```

The key thing is that this is **one single SQL statement** — the part that
picks which job to claim (the SELECT inside) and the part that actually
claims it (the UPDATE) happen together, not as two separate steps.

Why that matters: SQLite only lets one process write to the database at a
time — this is just how SQLite works, it's not something I configured. So
if two workers both call this function at basically the same moment, one
of them gets there first, does the whole UPDATE (pick a job + claim it) as
one uninterruptible thing, and finishes. The second worker was stuck
waiting for the lock. When it finally gets to run, the row the first
worker just took is no longer `pending` — so its own SELECT just doesn't
find that job anymore. It'll either grab a different job or get zero rows
back (`cur.rowcount == 0`), which is exactly what tells the worker "there
was nothing to claim, keep polling."

If I'd split this into a separate `SELECT` and then a separate `UPDATE`,
there'd be a gap between them where two workers could both read the same
"this job is free" result before either one updates it — that's the bug I
specifically avoided by keeping it as one statement.

The trade-off is that every single claim across the whole system happens
one at a time, since it's all going through one SQLite writer lock. For
this project that's totally fine. If this needed to handle a lot more load
someday, I'd look at Postgres, which has `SELECT ... FOR UPDATE SKIP
LOCKED` and doesn't have this single-writer limitation.

## 2. A worker is SIGKILLed halfway through a job. Walk through what happens and the worst-case delay.

Here's what actually happens, step by step:

1. A worker claims a job — its row is now `state='processing'`, with a
   heartbeat timestamp set to right now.
2. The worker starts running the job's command as its own subprocess, and
   kicks off a background thread that updates that heartbeat every 5
   seconds while the command is still running.
3. Someone runs `kill -9` on the worker. There's no way to catch this
   signal or clean anything up — the process is just gone instantly. The
   job's row is left exactly as it was: `processing`, with a heartbeat
   that's now frozen and will never update again from this worker. Also
   worth noting — the job's command itself keeps running as an orphaned
   process, since it wasn't killed directly, but nothing is around to
   collect its result anymore.
4. Nothing happens next unless there's another worker still alive. Every
   worker, roughly every 5 seconds, checks for any job that's been sitting
   in `processing` with a heartbeat older than 15 seconds, and assumes its
   worker must have died. It resets that job back to `pending`.
5. That same worker (or a different one) then picks it up again through
   the normal claiming process and runs it from scratch.

**Worst case:** 15 seconds (the heartbeat timeout) plus up to 5 more
seconds (how often the check runs) — so around 20 seconds. That's well
inside the 60 second limit. I actually tested this by hand and it was
recovering closer to 5-10 seconds in practice.

**One thing worth being upfront about:** this only works if _some_ worker
is still alive to notice. If you kill every single worker, the job just
sits there until you start a new one — which is fine given how the
assignment phrases it ("after restart"), since starting a worker is
exactly what kicks off the recovery check.

**Why I don't count this as a retry attempt:** getting killed by `kill -9`
says nothing about whether the job itself is broken — it's an
infrastructure problem, not the job's fault. If I counted it against
`max_retries`, a job could get unlucky with crash timing a couple of times
and get thrown in the DLQ for no reason related to what it actually does.
Only an actual bad exit code from the command counts against its retry
budget.

## 3. Does `dlq retry` reset attempts? Why?

Yes, it resets `attempts` back to 0.

My reasoning: by the time a job is in the DLQ, it's already used up its
full retry budget with real backoff delays in between. Retrying it from
the DLQ is something a person decides to do on purpose — usually because
they fixed whatever was actually wrong (bad input, a dependency that was
down, wrong config). Since it's basically getting a second chance, it
makes sense to give it a full fresh set of retries rather than, say, one
more shot before it's immediately sent back to the DLQ. Keeping the old
attempt count around would also mean I'd have to remember and explain a
weird special case just for DLQ jobs, which doesn't seem worth it — I'd
rather `dlq retry` behave the same as enqueueing it again from scratch.

## 4. What did you consider for `worker stop` and why did you go with pidfiles?

**What I used:** each worker writes a small file with its own process id
to `~/.queuectl/workers/` when it starts. `worker stop` just reads that
folder, checks which of those processes are actually still alive, and
sends them a normal `SIGTERM`.

**What I thought about instead:**

- **A socket that workers listen on**, and `worker stop` connects and
  sends a message. I decided against this because it's a lot of extra
  code (accepting connections, some kind of protocol) just to do
  something a plain OS signal already does. It also adds its own way to
  break — like a worker whose socket died but is otherwise fine.

- **A "please stop" flag in the database that workers poll for.** The
  problem here is you still need some way to know _which_ processes are
  workers in the first place (for `status`, for example), so I'd end up
  needing pidfiles anyway — this would just be doing the same thing in two
  places that could get out of sync.

- **Just relying on the terminal/shell to send the signal**, like Ctrl+C
  in the terminal that started the workers. This doesn't work at all for
  what the assignment actually requires, since `worker stop` has to work
  from a completely different terminal that has no relationship to the
  one that started the workers.

Pidfiles felt like the simplest option that actually satisfies "works from
a different terminal" without needing any extra moving parts.

## 5. If priorities were added tomorrow, what survives and what breaks?

**Would stay the same:**

- The whole atomic claiming approach doesn't care what order jobs come in
  — I'd just need to add `priority` to the `ORDER BY` in the claim query,
  and the reasoning about why claiming is safe across processes doesn't
  change at all.
- Retries, backoff, the DLQ, config, crash recovery, and `worker stop` are
  all separate from ordering — none of them look at which job gets picked,
  only what happens once a job is claimed.
- The CLI commands and the `--json` output format wouldn't need to change,
  other than `enqueue` accepting an optional priority field.

**Would need real changes:**

- I'd add a `priority` column, and the index that makes claiming fast
  would need to include it as well, otherwise claiming would get slower as
  the table grows.
- The bigger issue is starvation — if high priority jobs never stop
  coming in, low priority jobs could wait forever. Right now there's
  nothing in the design that handles that, so I'd need to actually think
  through something like bumping a job's effective priority the longer it
  waits, which is genuinely new logic, not just a small tweak.
- The JSON output from `list` would include a new `priority` field, so
  anything reading that output would need to just ignore fields it
  doesn't recognize.
