#!/bin/bash
export QUEUECTL_HOME=~/.queuectl_demo
rm -rf $QUEUECTL_HOME
echo "=== Reset: clean queue ==="
queuectl status
