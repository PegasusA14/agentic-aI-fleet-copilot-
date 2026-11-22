#!/bin/sh
# Container entry point: `fleet <command>`. The fleet database is built on first start into the /app/var volume.
set -eu

PY=/app/backend/.venv/bin/python
cd /app/backend

ensure_data() {
  if [ ! -f /app/var/fleet.db ]; then
    echo "Loading the telemetry dataset into /app/var/fleet.db ..."
    "$PY" -m fleet_copilot.data.cli
  fi
}

cmd="${1:-serve}"
[ "$#" -gt 0 ] && shift

case "$cmd" in
  serve)
    ensure_data
    echo "Fleet Copilot is starting. Open http://127.0.0.1:${FLEET_PORT:-8000} on this machine."
    exec "$PY" -m fleet_copilot.api
    ;;
  eval)
    ensure_data
    exec "$PY" -m fleet_copilot.evals run "$@"
    ;;
  eval-live)
    ensure_data
    exec "$PY" -m fleet_copilot.evals run --live "$@"
    ;;
  ask)
    ensure_data
    exec "$PY" -m fleet_copilot.cli ask "$@"
    ;;
  doctor)
    ensure_data
    exec "$PY" -m fleet_copilot.cli doctor "$@"
    ;;
  load-data)
    exec "$PY" -m fleet_copilot.data.cli "$@"
    ;;
  *)
    echo "usage: fleet [serve|eval|eval-live|ask|doctor|load-data] [args...]" >&2
    exit 2
    ;;
esac
