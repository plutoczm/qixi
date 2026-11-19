#!/bin/sh
set -eu
if [ "${XUNQI_DATA_MODE:-live}" != "offline" ] && [ "${XUNQI_DATA_MODE:-live}" != "live" ]; then
  echo "XUNQI_DATA_MODE must be offline or live" >&2
  exit 2
fi
export XUNQI_DATA_MODE
if [ "${XUNQI_DATA_MODE}" = "live" ] && [ -z "${XUNQI_HOTEL_SOURCE:-}" ]; then
  export XUNQI_HOTEL_SOURCE=rollinggo
fi
exec python -m uvicorn stay_agent.main:app \
  --app-dir backend \
  --host 0.0.0.0 \
  --port "${XUNQI_PORT:-8000}" \
  --no-access-log
