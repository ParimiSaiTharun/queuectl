#!/bin/bash
export QUEUECTL_HOME=~/.queuectl_demo

echo ""
echo "=== Part 3: many jobs, multiple workers, exactly-once ==="
rm -f /tmp/demo.log
for i in $(seq 1 10); do
  queuectl enqueue "{\"command\":\"echo $i >> /tmp/demo.log\"}"
done

queuectl worker start --count 4 &
WPID=$!
sleep 3
echo "--- lines written (should be exactly 10, no duplicates) ---"
wc -l /tmp/demo.log
sort /tmp/demo.log | uniq -c

kill -TERM $WPID
wait $WPID 2>/dev/null
rm -f /tmp/demo.log