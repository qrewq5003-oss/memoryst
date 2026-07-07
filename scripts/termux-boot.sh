#!/data/data/com.termux/files/usr/bin/bash
# Termux autostart script for memoryst
# Place in ~/.termux/boot/ to run on device startup
# Requires: Termux:Boot app installed (F-Droid/Play) AND opened once to grant
# permissions - without it, Android never runs anything in ~/.termux/boot/,
# this script included, no matter how it's configured.
# Also requires: termux-boot package (pkg install termux-boot)

# Deliberately not derived from $0/dirname: this script is installed to
# ~/.termux/boot/memoryst.sh, two directory levels below $HOME, but the repo
# checkout it needs to run from is always directly at ~/memoryst (see
# termux-setup.sh). A dirname-based path silently pointed at the wrong
# directory here for a long time - "$HOME/memoryst" can't drift that way.
DIR="$HOME/memoryst"
LOG="$DIR/data/termux.log"

# Wait for network
sleep 10

# Start memoryst
cd "$DIR"
.venv/bin/python -m app.main >> "$LOG" 2>&1 &
echo "memoryst started (PID: $!) at $(date)" >> "$LOG"

# Start crond if it isn't already running. crond is killed on every reboot
# like everything else in the Termux sandbox and does not restart itself, so
# without this the daily backup cron job (see scripts/backup_db.py /
# `crontab -l`) silently stops firing after every reboot. The pgrep guard
# avoids spawning a second daemon if this hook ever fires more than once in
# the same boot (observed to happen with some Termux:Boot broadcast timing).
if ! pgrep -x crond >/dev/null 2>&1; then
    crond
    echo "crond started at $(date)" >> "$LOG"
else
    echo "crond already running, skipped at $(date)" >> "$LOG"
fi
