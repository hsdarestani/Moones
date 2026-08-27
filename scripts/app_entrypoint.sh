#!/bin/sh
set -eu

if [ -f /app/scripts/host_route_fix.py ]; then
  echo 'MOONES_APP_ENTRYPOINT applying confirmed host route fix'
  python /app/scripts/host_route_fix.py || true
fi

if [ -f /app/scripts/nginx_route_repair.py ]; then
  python /app/scripts/nginx_route_repair.py || true
fi

exec "$@"
