#!/bin/bash
export QUEUECTL_HOME=~/.queuectl_demo

echo ""
echo "=== Part 5: jobs survive a restart ==="
queuectl enqueue '{"id":"p1","command":"echo one"}'
queuectl enqueue '{"id":"p2","command":"echo two"}'
echo "--- pending before restart ---"
queuectl list

queuectl worker start &
WPID=$!
sleep 2
echo "--- completed after (simulated) restart ---"
queuectl list

kill -TERM $WPID
wait $WPID 2>/dev/null