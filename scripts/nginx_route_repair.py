#!/usr/bin/env python3
import os
import re
import signal
from datetime import datetime, timezone
from pathlib import Path

NGINX_ROOT = Path("/host/etc/nginx")
TARGET = "http://127.0.0.1:18000"


def log(message: str) -> None:
    print(f"MOONES_NGINX_REPAIR {message}", flush=True)


def proc_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def matching_block_end(text: str, open_brace: int) -> int | None:
    depth = 0
    for idx in range(open_brace, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return idx + 1
    return None


def blocks_for_keyword(text: str, keyword_pattern: str):
    for match in re.finditer(keyword_pattern, text, flags=re.I):
        brace = text.find("{", match.start())
        if brace < 0:
            continue
        end = matching_block_end(text, brace)
        if end is not None:
            yield match.start(), end, text[match.start():end]


def replace_root_location(server_block: str) -> tuple[str, bool]:
    # Only replace an exact `location / { ... }`, never /api, /static, regex locations, etc.
    match = re.search(r"\blocation\s+/\s*\{", server_block, flags=re.I)
    if not match:
        return server_block, False
    brace = server_block.find("{", match.start())
    end = matching_block_end(server_block, brace)
    if end is None:
        return server_block, False

    old_location = server_block[match.start():end]
    # A redirect-only block is not the application upstream; do not alter it.
    if "proxy_pass" not in old_location:
        return server_block, False

    indent_match = re.search(r"(^[ \t]*)location\s+/\s*\{", server_block[match.start():], flags=re.I | re.M)
    indent = indent_match.group(1) if indent_match else "    "
    inner = indent + "    "
    new_location = (
        f"{indent}location / {{\n"
        f"{inner}proxy_pass {TARGET};\n"
        f"{inner}proxy_http_version 1.1;\n"
        f"{inner}proxy_set_header Host $host;\n"
        f"{inner}proxy_set_header X-Real-IP $remote_addr;\n"
        f"{inner}proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"{inner}proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"{indent}}}"
    )
    return server_block[:match.start()] + new_location + server_block[end:], True


def nginx_master_pids() -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        cmdline = proc_cmdline(int(entry.name)).lower()
        if "nginx: master process" in cmdline:
            found.append(int(entry.name))
    return found


def main() -> int:
    if not NGINX_ROOT.exists():
        log("skip nginx_root_missing")
        return 0

    paths = []
    for dirname in ("sites-enabled", "sites-available", "conf.d"):
        root = NGINX_ROOT / dirname
        if root.exists():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    if (NGINX_ROOT / "nginx.conf").is_file():
        paths.append(NGINX_ROOT / "nginx.conf")

    unique = {}
    for path in paths:
        try:
            unique[str(path.resolve())] = path
        except Exception:
            unique[str(path)] = path

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = NGINX_ROOT / "moones-recovery-backups" / timestamp
    changed = []
    matched_server = False

    for path in unique.values():
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        if "moones.top" not in text:
            continue

        replacements = []
        for start, end, block in blocks_for_keyword(text, r"\bserver\s*\{"):
            if not re.search(r"\bserver_name\b[^;]*\bmoones\.top\b[^;]*;", block, flags=re.I | re.S):
                continue
            matched_server = True
            repaired, did_change = replace_root_location(block)
            if did_change:
                replacements.append((start, end, repaired))

        if not replacements:
            continue

        updated = text
        for start, end, repaired in reversed(replacements):
            updated = updated[:start] + repaired + updated[end:]

        try:
            backup_root.mkdir(parents=True, exist_ok=True)
            safe_name = str(path).replace("/", "__").strip("_") or "nginx.conf"
            (backup_root / safe_name).write_text(text)
            path.write_text(updated)
            changed.append(path)
            log(f"changed file={path}")
        except Exception as exc:
            log(f"write_failed file={path} error={exc!r}")
            return 0

    if not matched_server:
        log("skip moones_server_block_not_found")
        return 0
    if not changed:
        log("skip moones_server_found_no_proxy_root_location")
        return 0

    masters = nginx_master_pids()
    if not masters:
        log("changed_but_nginx_master_not_found")
        return 0

    for pid in masters:
        try:
            os.kill(pid, signal.SIGHUP)
            log(f"reloaded pid={pid}")
        except Exception as exc:
            log(f"reload_failed pid={pid} error={exc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
