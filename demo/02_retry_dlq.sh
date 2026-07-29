#!/bin/bash
export QUEUECTL_HOME=~/.queuectl_demo

echo ""
echo "=== Part 2: retry with backoff, lands in DLQ, manual retry ==="
queuectl config set backoff-base 1
queuectl enqueue '{"id":"bad","command":"exit 1","max_retries":2}'

queuectl worker start &
WPID=$!
sleep 4
echo "--- dlq after exhausting retries ---"
queuectl dlq list

queuectl dlq retry bad
sleep 1
echo "--- job re-enqueued, attempts reset ---"
queuectl list --state pending

kill -TERM $WPID
wait $WPID 2>/dev/null