#!/bin/sh
set -eu

if [ -f /app/scripts/nginx_route_repair.py ]; then
  python /app/scripts/nginx_route_repair.py || true
fi

exec "$@"
