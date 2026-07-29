#!/bin/bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

bash "$DIR/00_reset.sh"
bash "$DIR/01_basic_job.sh"
bash "$DIR/02_retry_dlq.sh"
bash "$DIR/03_concurrency.sh"
bash "$DIR/04_crash_recovery.sh"
bash "$DIR/05_restart_persistence.sh"

echo ""
echo "=== Final status ==="
queuectl status