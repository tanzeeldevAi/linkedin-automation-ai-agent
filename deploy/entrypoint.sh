#!/usr/bin/env bash
# Container entrypoint. Prepares /data, sets the VNC password from the
# environment, then hands over to supervisor.
set -euo pipefail

mkdir -p /data/chrome-profile /data/shots

# The VNC password is the second lock on the remote browser view (Traefik basic
# auth is the first). Refuse to start without one -- an unauthenticated VNC into
# a browser logged into LinkedIn is the worst possible failure here.
if [[ -z "${VNC_PASSWORD:-}" ]]; then
  echo "FATAL: VNC_PASSWORD is not set. Refusing to expose an unlocked browser." >&2
  exit 1
fi
x11vnc -storepasswd "${VNC_PASSWORD}" /data/.vncpass >/dev/null 2>&1
chmod 600 /data/.vncpass

if [[ -z "${DASHBOARD_PASSWORD:-}" ]]; then
  echo "FATAL: DASHBOARD_PASSWORD is not set. Refusing to serve the dashboard." >&2
  exit 1
fi

# Create/upgrade the database before anything tries to read it.
python /app/run.py init

# On the very first boot there is no LinkedIn session yet, so start paused
# rather than letting the worker walk into a login wall and trip its own
# breaker. Cleared by `run.py login`, or by pressing Start on the dashboard
# once you have signed in through the browser view.
python - <<'PY'
from la import db
db.init()
if db.get_state("first_boot_done") != "1":
    db.pause("waiting for the first LinkedIn login -- sign in via the browser view, then press Start")
    db.set_state("first_boot_done", "1")
    print("first boot: agent is paused until you log in")
PY

echo "--- config check ---"
python /app/run.py doctor || true
echo "--- starting supervisor ---"

exec /usr/bin/supervisord -c /app/deploy/supervisord.conf
