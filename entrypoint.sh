#!/bin/bash
set -e

echo "Starting cfs.py (fake APRS-IS server)..."
CFS_LOG="/opt/aprstac/cfs.log"

# Debug: check python3 and cfs.py exist
python3 --version >/dev/null 2>&1 || echo "WARNING: python3 not found!"
ls -la /usr/local/bin/cfs.py 2>&1 || echo "WARNING: cfs.py not found!"

/usr/bin/python3 /usr/local/bin/cfs.py >> "$CFS_LOG" 2>&1 &
CFS_PID=$!
echo "cfs.py started with PID $CFS_PID, log: $CFS_LOG"

APSTAC_BIN=""
for path in /usr/bin/aprstac-server /usr/local/bin/aprstac-server /opt/aprstac/aprstac-server; do
    if [ -x "$path" ]; then
        APSTAC_BIN="$path"
        break
    fi
done
if [ -z "$APSTAC_BIN" ]; then
    echo "ERROR: aprstac-server not found!"
    exit 1
fi
echo "Found aprstac-server: $APSTAC_BIN"

cd /opt/aprstac

if [ ! -f aprstac.toml ]; then
    echo "Generating default config..."
    timeout 3 "$APSTAC_BIN" 2>/dev/null || true
    sleep 1
fi

# Force listen on 0.0.0.0
sed -i 's/^listen_host\s*=.*/listen_host = "0.0.0.0"/' aprstac.toml

# Enable local network access in SQLite
DB_PATH=$(grep -oP '^database_path\s*=\s*"\K[^"]+' aprstac.toml 2>/dev/null || echo "data/aprstac.db")
mkdir -p "$(dirname "$DB_PATH")"
sqlite3 "$DB_PATH" "INSERT OR REPLACE INTO settings (id, key, value, updated_at) VALUES (4, 'network_local_access', 'true', datetime('now'));"

echo "=== aprstac.toml ==="
cat aprstac.toml
echo "=== End ==="

cleanup() {
    echo "Shutting down..."
    kill $CFS_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "Starting aprstac-server..."
exec "$APSTAC_BIN"
