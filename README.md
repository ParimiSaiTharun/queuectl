# queuectl

This is a background job queue you control entirely from the command line.
You enqueue jobs, start one or more workers, and they run your jobs, retrying
failed ones automatically before giving up and moving them to a dead letter
queue. Everything is saved to disk so nothing is lost if something crashes
or you restart your machine.

I built this for the QueueCTL backend internship assignment. The design
decisions and trade-offs (why SQLite, how crash recovery works, etc.) are
written up in DECISIONS.md — that's probably more useful to read than this
file if you want to understand _why_ things are built this way.

## Getting it running

You need Python 3.8+. No external libraries are required, just the
standard library.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

That installs a `queuectl` command you can run from anywhere (while the
venv is active).

By default everything gets stored in `~/.queuectl/queue.db`. If you want to
point it somewhere else (useful if you're testing and don't want to mess up
your real queue):

```bash
export QUEUECTL_HOME=/some/other/folder
```

## How to use it

Add a job:

```bash
queuectl enqueue '{"command":"echo Hello World"}'
```

You can also give it your own id and override how many times it retries:

```bash
queuectl enqueue '{"id":"my-job","command":"sleep 2","max_retries":5}'
```

Start some workers (this runs in the foreground and blocks, so use a
separate terminal for everything else):

```bash
queuectl worker start --count 3
```

From another terminal:

```bash
queuectl status
queuectl list --state pending
queuectl list --state dead --json
```

Stop the workers cleanly (also works from a different terminal than the
one that started them):

```bash
queuectl worker stop
```

Dead letter queue:

```bash
queuectl dlq list
queuectl dlq retry my-job
```

Change config (how many retries, how the backoff delay grows):

```bash
queuectl config set max-retries 5
queuectl config set backoff-base 3
queuectl config get
```

### Demo

https://drive.google.com/file/d/1PZXfOmrr0HrWMFgpETLtCD8vuloIb7lb/view?usp=sharing

## How it's put together
