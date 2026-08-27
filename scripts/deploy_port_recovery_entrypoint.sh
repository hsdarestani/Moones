#!/usr/bin/env bash
set -Eeuo pipefail

recover_moones_port() {
  if [[ "${MOONES_PORT_RECOVERY:-0}" != "1" ]]; then
    return 0
  fi

  if ! command -v nsenter >/dev/null 2>&1 || ! command -v ss >/dev/null 2>&1; then
    echo "MOONES_PORT_RECOVERY tooling_missing=true"
    return 0
  fi

  local listener pid cmd cwd cgroup unit combined
  listener="$(nsenter -t 1 -n ss -ltnp 'sport = :8000' 2>/dev/null | tail -n +2 | head -n 1 || true)"
  if [[ -z "$listener" ]]; then
    echo "MOONES_PORT_RECOVERY port_8000=free"
    return 0
  fi

  echo "MOONES_PORT_RECOVERY port_8000=occupied listener=${listener}"
  pid="$(printf '%s\n' "$listener" | grep -oE 'pid=[0-9]+' | head -n 1 | cut -d= -f2 || true)"
  if [[ -z "$pid" || ! -r "/proc/$pid/cmdline" ]]; then
    echo "MOONES_PORT_RECOVERY safe_action=none reason=owner_pid_unresolved"
    return 0
  fi

  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  cgroup="$(cat "/proc/$pid/cgroup" 2>/dev/null || true)"
  combined="${cmd} ${cwd}"
  unit="$(printf '%s\n' "$cgroup" | sed -nE 's#.*system\.slice/([^/]+\.service).*#\1#p' | head -n 1 || true)"

  echo "MOONES_PORT_RECOVERY owner_pid=$pid owner_cmd=${cmd:-unknown} owner_cwd=${cwd:-unknown} owner_unit=${unit:-none}"

  if [[ "$unit" =~ ^(mones|moones)[A-Za-z0-9_.@-]*\.service$ ]]; then
    echo "MOONES_PORT_RECOVERY action=disable_legacy_service unit=$unit"
    nsenter -t 1 -m -u -i -n -p --root=/proc/1/root --wd=/ systemctl disable --now "$unit"
  elif [[ "$combined" =~ (uvicorn|gunicorn) ]] && [[ "$combined" =~ (app\.main:app|[Mm]oones|[Mm]ones) ]]; then
    echo "MOONES_PORT_RECOVERY action=terminate_stale_moones_process pid=$pid"
    kill -TERM "$pid" 2>/dev/null || true
  else
    echo "MOONES_PORT_RECOVERY safe_action=none reason=listener_not_recognized_as_moones"
    return 0
  fi

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! nsenter -t 1 -n ss -ltn 'sport = :8000' 2>/dev/null | tail -n +2 | grep -q .; then
      echo "MOONES_PORT_RECOVERY result=port_8000_freed"
      return 0
    fi
    sleep 0.5
  done

  listener="$(nsenter -t 1 -n ss -ltnp 'sport = :8000' 2>/dev/null | tail -n +2 | head -n 1 || true)"
  echo "MOONES_PORT_RECOVERY result=port_8000_still_occupied listener=${listener:-unknown}"
  return 0
}

recover_moones_port
exec "$@"
