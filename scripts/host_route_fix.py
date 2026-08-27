#!/usr/bin/env python3
import os
import re
import signal
from datetime import datetime, timezone
from pathlib import Path

NGINX_ROOT = Path("/host/etc/nginx")
TARGET_FILES = [
    NGINX_ROOT / "sites-enabled" / "moones",
    NGINX_ROOT / "sites-available" / "moones",
]


def log(message: str) -> None:
    print(f"MOONES_ROUTE_FIX {message}", flush=True)


def nginx_master_pids() -> list[int]:
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").lower()
        except Exception:
            continue
        if "nginx: master process" in cmd:
            result.append(int(entry.name))
    return result


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = NGINX_ROOT / "moones-recovery-backups" / timestamp
    changed = []

    for path in TARGET_FILES:
        if not path.is_file():
            log(f"skip_missing file={path}")
            continue
        try:
            original = path.read_text(errors="replace")
        except Exception as exc:
            log(f"read_failed file={path} error={exc!r}")
            return 1

        if not re.search(r"\bserver_name\s+moones\.top\s*;", original, flags=re.I):
            log(f"refusing_unexpected_server file={path}")
            return 1

        updated, count = re.subn(
            r"proxy_pass(\s+)http://127\.0\.0\.1:(?:8000|8002)\s*;",
            r"proxy_pass\1http://127.0.0.1:18000;",
            original,
            flags=re.I,
        )

        remaining_old = re.findall(r"proxy_pass\s+http://127\.0\.0\.1:(?:8000|8002)\s*;", updated, flags=re.I)
        if remaining_old:
            log(f"refusing_incomplete_rewrite file={path} remaining={len(remaining_old)}")
            return 1

        if count == 0:
            if "proxy_pass http://127.0.0.1:18000;" in updated:
                log(f"already_fixed file={path}")
                continue
            log(f"refusing_no_known_upstream file={path}")
            return 1

        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / path.parent.name
        backup_path.write_text(original)

        mode = path.stat().st_mode & 0o777
        temp_path = path.with_name(path.name + ".moones-tmp")
        temp_path.write_text(updated)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        changed.append(path)
        log(f"rewritten file={path} directives={count} target=127.0.0.1:18000")

    if not changed:
        log("route_already_correct")
    masters = nginx_master_pids()
    if not masters:
        log("nginx_master_not_found")
        return 1

    for pid in masters:
        try:
            os.kill(pid, signal.SIGHUP)
            log(f"nginx_reloaded pid={pid}")
        except Exception as exc:
            log(f"nginx_reload_failed pid={pid} error={exc!r}")
            return 1

    log("route_fix_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
