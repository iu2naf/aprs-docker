#!/bin/bash
set -e

CFS_SCRIPT="/usr/local/bin/cfs.py"
CFS_LOG="${CFS_LOG:-/opt/aprstac/logs/cfs.log}"
CFS_PID_FILE="/opt/aprstac/logs/cfs.pid"
SUPERVISOR_PID_FILE="/opt/aprstac/logs/cfs-supervisor.pid"

export CFS_LOG_DIR="${CFS_LOG_DIR:-/opt/aprstac/logs}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

start_cfs() {
    mkdir -p "$(dirname "$CFS_LOG")"
    (
        while :; do
            /usr/bin/python3 "$CFS_SCRIPT" >> "$CFS_LOG" 2>&1 &
            echo $! > "$CFS_PID_FILE"
            wait $! || true
            code=$?
            log "cfs.py terminated (code $code); restarting in 2s..."
            sleep 2
        done
    ) &
    echo $! > "$SUPERVISOR_PID_FILE"
    log "cfs.py supervisor started (pid $(cat "$SUPERVISOR_PID_FILE"))"
}

stop_cfs() {
    if [ -f "$SUPERVISOR_PID_FILE" ]; then
        kill "$(cat "$SUPERVISOR_PID_FILE")" 2>/dev/null || true
    fi
    if [ -f "$CFS_PID_FILE" ]; then
        kill "$(cat "$CFS_PID_FILE")" 2>/dev/null || true
    fi
}

wait_for_cfs() {
    log "Waiting for cfs.py on ports 14580/14581..."
    for _ in $(seq 1 30); do
        if python3 - <<'PYEOF' 2>/dev/null
import socket
for port in (14580, 14581):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(('127.0.0.1', port))
        s.close()
    except OSError:
        raise SystemExit(1)
raise SystemExit(0)
PYEOF
        then
            log "cfs.py is ready."
            return 0
        fi
        sleep 1
    done
    log "WARNING: cfs.py did not become ready in time"
    return 1
}

log "Starting cfs.py (fake APRS-IS server)..."
python3 --version >/dev/null 2>&1 || echo "WARNING: python3 not found!"
ls -la "$CFS_SCRIPT" 2>&1 || echo "WARNING: cfs.py not found!"

start_cfs
wait_for_cfs || true

APSTAC_BIN=""
for path in /usr/bin/aprstac-server /usr/local/bin/aprstac-server /opt/aprstac/aprstac-server; do
    if [ -x "$path" ]; then
        APSTAC_BIN="$path"
        break
    fi
done
if [ -z "$APSTAC_BIN" ]; then
    log "ERROR: aprstac-server not found!"
    stop_cfs
    exit 1
fi
log "Found aprstac-server: $APSTAC_BIN"

cd /opt/aprstac

if [ ! -f aprstac.toml ]; then
    log "Generating default config..."
    timeout 3 "$APSTAC_BIN" >/dev/null 2>&1 || true
    sleep 1
fi

# Force listen on 0.0.0.0 (aggiunge la riga se assente)
if grep -q '^listen_host' aprstac.toml; then
    sed -i 's/^listen_host\s*=.*/listen_host = "0.0.0.0"/' aprstac.toml
else
    echo 'listen_host = "0.0.0.0"' >> aprstac.toml
fi

# Enable local network access in SQLite (solo se la tabella settings esiste)
DB_PATH=$(grep -oP '^database_path\s*=\s*"\K[^"]+' aprstac.toml 2>/dev/null || echo "data/aprstac.db")
mkdir -p "$(dirname "$DB_PATH")"
if sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='table' AND name='settings';" 2>/dev/null | grep -q settings; then
    sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO settings (id, key, value, updated_at) VALUES (4, 'network_local_access', 'true', datetime('now'));" \
        || log "WARNING: unable to update network_local_access"
else
    log "INFO: settings table not present yet, skipping network_local_access"
fi

log "=== aprstac.toml ==="
cat aprstac.toml
log "=== End ==="

cleanup() {
    log "Shutting down..."
    kill "$APSTAC_PID" 2>/dev/null || true
    stop_cfs
    exit 0
}
trap cleanup SIGTERM SIGINT

log "Starting aprstac-server..."
"$APSTAC_BIN" &
APSTAC_PID=$!

# Attende la chiusura del processo (trap gestisce SIGTERM/SIGINT)
while kill -0 "$APSTAC_PID" 2>/dev/null; do
    sleep 2
done
wait "$APSTAC_PID"
