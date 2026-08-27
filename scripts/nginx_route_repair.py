#!/usr/bin/env python3
import http.client
import re
import subprocess
from pathlib import Path

NGINX_ROOT = Path("/host/etc/nginx")
REPORT_PATH = Path("/diagnostics/host-routing.txt")


def block_end(text: str, open_brace: int) -> int | None:
    depth = 0
    for idx in range(open_brace, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return idx + 1
    return None


def blocks(text: str, pattern: str):
    for match in re.finditer(pattern, text, flags=re.I):
        brace = text.find("{", match.start())
        if brace < 0:
            continue
        end = block_end(text, brace)
        if end is not None:
            yield text[match.start():end]


def proc_cmdline(pid: int) -> str:
    try:
        value = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        return value[:300]
    except Exception:
        return ""


def local_probe(port: int, path: str, host: str = "moones.top") -> str:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "MoonesHostDiagnostic/1.0", "Connection": "close"})
        response = conn.getresponse()
        body = response.read(100000).decode("utf-8", "replace").lower()
        markers = []
        for marker in ("config.urls", "tg/webhook/", "automation/", "safety/", '"status":"ok"', "moones"):
            if marker in body:
                markers.append(marker)
        return f"status={response.status} server={response.getheader('Server')!r} markers={markers}"
    except Exception as exc:
        return f"error={type(exc).__name__}:{exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    lines: list[str] = []
    lines.append("MOONES_HOST_ROUTING_DIAGNOSTIC v1")
    lines.append(f"nginx_root_exists={NGINX_ROOT.exists()}")

    candidate_paths: list[Path] = []
    if NGINX_ROOT.exists():
        for dirname in ("sites-enabled", "sites-available", "conf.d"):
            root = NGINX_ROOT / dirname
            if root.exists():
                candidate_paths.extend(path for path in root.rglob("*") if path.is_file())
        main_conf = NGINX_ROOT / "nginx.conf"
        if main_conf.is_file():
            candidate_paths.append(main_conf)

    unique: dict[str, Path] = {}
    for path in candidate_paths:
        try:
            unique[str(path.resolve())] = path
        except Exception:
            unique[str(path)] = path

    moones_blocks = 0
    for path in unique.values():
        try:
            text = path.read_text(errors="replace")
        except Exception as exc:
            lines.append(f"nginx_read_error path={path} error={type(exc).__name__}")
            continue
        if "moones.top" not in text:
            continue
        for server_block in blocks(text, r"\bserver\s*\{"):
            if not re.search(r"\bserver_name\b[^;]*\bmoones\.top\b[^;]*;", server_block, flags=re.I | re.S):
                continue
            moones_blocks += 1
            listens = [item.strip() for item in re.findall(r"\blisten\s+([^;]+);", server_block, flags=re.I)]
            server_names = [" ".join(item.split()) for item in re.findall(r"\bserver_name\s+([^;]+);", server_block, flags=re.I)]
            proxies = [item.strip() for item in re.findall(r"\bproxy_pass\s+([^;]+);", server_block, flags=re.I)]
            returns = [" ".join(item.split()) for item in re.findall(r"\breturn\s+([^;]+);", server_block, flags=re.I)]
            root_locations = []
            for location_block in blocks(server_block, r"\blocation\s+/\s*\{"):
                location_proxies = [item.strip() for item in re.findall(r"\bproxy_pass\s+([^;]+);", location_block, flags=re.I)]
                location_returns = [" ".join(item.split()) for item in re.findall(r"\breturn\s+([^;]+);", location_block, flags=re.I)]
                root_locations.append({"proxy_pass": location_proxies, "return": location_returns})
            lines.append(
                f"nginx_moones_block file={path} listen={listens} server_name={server_names} "
                f"proxy_pass={proxies} return={returns} root_locations={root_locations}"
            )
    lines.append(f"nginx_moones_block_count={moones_blocks}")

    try:
        ss = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=5, check=False)
        interesting = []
        for row in ss.stdout.splitlines():
            if re.search(r":(?:80|443|8000|18000)\b", row):
                interesting.append(" ".join(row.split()))
        lines.append("listeners_begin")
        lines.extend(interesting or ["none"])
        lines.append("listeners_end")
    except Exception as exc:
        lines.append(f"listeners_error={type(exc).__name__}:{exc}")

    process_keywords = ("nginx", "caddy", "apache", "httpd", "gunicorn", "uvicorn", "daphne", "manage.py", "django")
    process_rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        cmdline = proc_cmdline(int(entry.name))
        lowered = cmdline.lower()
        if cmdline and any(keyword in lowered for keyword in process_keywords):
            process_rows.append(f"pid={entry.name} cmd={cmdline!r}")
    lines.append("processes_begin")
    lines.extend(process_rows or ["none"])
    lines.append("processes_end")

    for port in (8000, 18000):
        for path in ("/health", "/health/", "/static/pitch.html", "/"):
            lines.append(f"probe port={port} path={path} {local_probe(port, path)}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print("MOONES_HOST_DIAGNOSTIC_WRITTEN", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
