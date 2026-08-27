#!/bin/sh
set -eu

if [ -d /diagnostics ]; then
  i=0
  while [ "$i" -lt 20 ] && [ ! -s /diagnostics/host-routing.txt ]; do
    i=$((i + 1))
    sleep 0.25
  done
  if [ -s /diagnostics/host-routing.txt ]; then
    echo '=== MOONES HOST ROUTING DIAGNOSTIC ==='
    cat /diagnostics/host-routing.txt
    echo '=== END MOONES HOST ROUTING DIAGNOSTIC ==='
  fi
fi

exec "$@"
