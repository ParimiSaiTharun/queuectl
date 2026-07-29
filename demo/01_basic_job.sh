#!/bin/bash
export QUEUECTL_HOME=~/.queuectl_demo

echo ""
echo "=== Part 1: basic job completes ==="
queuectl enqueue '{"command":"echo Hello World"}'

queuectl worker start &
WPID=$!
sleep 2
queuectl list
kill -TERM $WPID
wait $WPID 2>/dev/null
