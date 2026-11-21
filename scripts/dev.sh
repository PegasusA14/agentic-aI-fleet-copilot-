#!/bin/sh
# Dev mode: the API is on 127.0.0.1:8000 and Vite on 127.0.0.1:5173, which proxies /api to it.
set -eu
set -m  # job control: each background job gets its own process group
cd "$(dirname "$0")/.."

make api &
api_pid=$!
make frontend-dev &
web_pid=$!

cleanup() {
  trap '' INT TERM EXIT
  # a negative pid signals the whole group, so uv, python and node stop too
  kill -TERM -- "-$api_pid" "-$web_pid" 2>/dev/null || kill "$api_pid" "$web_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Stop as soon as either process exits (POSIX sh has no `wait -n`).
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done
