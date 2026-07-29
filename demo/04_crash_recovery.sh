#!/bin/bash
export QUEUECTL_HOME=~/.queuectl_demo

echo ""
echo "=== Part 4: worker SIGKILLed mid-job, recovers automatically ==="
queuectl enqueue '{"id":"slow","command":"sleep 8"}'

queuectl worker start &
WPID=$!
sleep 2
echo "--- job is processing, now killing the worker with SIGKILL ---"
kill -9 $WPID
sleep 1
echo "--- job stuck in processing, no worker alive ---"
queuectl list

echo "--- starting a new worker triggers recovery ---"
queuectl worker start &
WPID2=$!
sleep 22
echo "--- job recovered and completed, well under 60s ---"
queuectl list

kill -TERM $WPID2
wait $WPID2 2>/dev/null