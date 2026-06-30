#!/data/data/com.termux/files/usr/bin/bash
# Termux autostart script for memoryst
# Place in ~/.termux/boot/ to run on device startup
# Requires: termux-boot package (pkg install termux-boot)

DIR="$(cd "$(dirname "$0")/.." && pwd)/memoryst"
LOG="$DIR/data/termux.log"

# Wait for network
sleep 10

# Start memoryst
cd "$DIR"
.venv/bin/python -m app.main >> "$LOG" 2>&1 &
echo "memoryst started (PID: $!) at $(date)" >> "$LOG"
