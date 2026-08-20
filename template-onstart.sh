#!/usr/bin/env bash
set -euo pipefail

: "${PYWORKER_REPO:?Set PYWORKER_REPO to the public CallRedact PyWorker git repository URL}"
WORK=/opt/callredact-pyworker
BOOTLOG=/var/log/callredact-bootstrap.log

# Required by the Vast Serverless PyWorker SDK. The template exposes 3000/TCP.
export WORKER_PORT="${WORKER_PORT:-3000}"

# Vast SSH/Jupyter launch modes replace the image ENTRYPOINT. This script is
# intentionally short: fetch the PyWorker bundle and start it in the background,
# then return so the Vast instance bootstrap can finish normally.
if ! command -v git >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git ca-certificates
fi

rm -rf "$WORK"
git clone --depth 1 "$PYWORKER_REPO" "$WORK"
chmod +x "$WORK/start-server.sh"

mkdir -p /var/log
: > "$BOOTLOG"
nohup "$WORK/start-server.sh" >>"$BOOTLOG" 2>&1 </dev/null &
echo "CallRedact PyWorker bootstrap launched as PID $!"
