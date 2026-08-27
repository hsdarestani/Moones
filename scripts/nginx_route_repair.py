#!/usr/bin/env python3
import http.client
import json
import os
import re
import socket
from pathlib import Path

NGINX_ROOT = Path("/host/etc/nginx")
PROC_ROOT = Path("/host/proc") if Path("/host/proc").exists() else Path("/proc")
DOCKER_SOCKET = "/var/run/docker.sock"
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
        path = PROC_ROOT / str(pid) / "cmdline"
        value = path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        return value[:500]
    except Exception:
        return ""


def docker_request(path: str) -> tuple[int, bytes]:
    if not os.path.exists(DOCKER_SOCKET):
        return 0, b""

    class UnixHTTPConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect(DOCKER_SOCKET)

    conn = UnixHTTPConnection("localhost", timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()
    except Exception:
        return 0, b""
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    lines: list[str] = []
    lines.append("MOONES_HOST_ROUTING_DIAGNOSTIC v2")
    lines.append(f"nginx_root_exists={NGINX_ROOT.exists()}")
    lines.append(f"host_proc_exists={Path('/host/proc').exists()}")
    lines.append(f"docker_socket_exists={os.path.exists(DOCKER_SOCKET)}")

    candidate_paths: list[Path] = []
    if NGINX_ROOT.exists():
        for dirname in ("sites-enabled", "sites-available", "conf.d"):
            root = NGINX_ROOT / dirname
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_symlink():
                    # Absolute /etc/nginx symlinks are broken inside /host; the
                    # matching sites-available file is scanned separately.
                    continue
                if path.is_file():
                    candidate_paths.append(path)
        main_conf = NGINX_ROOT / "nginx.conf"
        if main_conf.is_file():
            candidate_paths.append(main_conf)

    moones_blocks = 0
    for path in candidate_paths:
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
            fastcgi = [item.strip() for item in re.findall(r"\bfastcgi_pass\s+([^;]+);", server_block, flags=re.I)]
            uwsgi = [item.strip() for item in re.findall(r"\buwsgi_pass\s+([^;]+);", server_block, flags=re.I)]
            returns = [" ".join(item.split()) for item in re.findall(r"\breturn\s+([^;]+);", server_block, flags=re.I)]
            locations = []
            for loc_match in re.finditer(r"\blocation\s+([^\{]+)\{", server_block, flags=re.I):
                brace = server_block.find("{", loc_match.start())
                end = block_end(server_block, brace)
                if end is None:
                    continue
                location_block = server_block[loc_match.start():end]
                locations.append({
                    "match": " ".join(loc_match.group(1).split()),
                    "proxy_pass": [x.strip() for x in re.findall(r"\bproxy_pass\s+([^;]+);", location_block, flags=re.I)],
                    "fastcgi_pass": [x.strip() for x in re.findall(r"\bfastcgi_pass\s+([^;]+);", location_block, flags=re.I)],
                    "uwsgi_pass": [x.strip() for x in re.findall(r"\buwsgi_pass\s+([^;]+);", location_block, flags=re.I)],
                    "return": [" ".join(x.split()) for x in re.findall(r"\breturn\s+([^;]+);", location_block, flags=re.I)],
                })
            lines.append(
                f"nginx_moones_block file={path} listen={listens} server_name={server_names} "
                f"proxy_pass={proxies} fastcgi_pass={fastcgi} uwsgi_pass={uwsgi} return={returns} locations={locations}"
            )
    lines.append(f"nginx_moones_block_count={moones_blocks}")

    process_keywords = (
        "nginx", "caddy", "apache", "httpd", "gunicorn", "uvicorn",
        "daphne", "manage.py", "django", "traefik", "haproxy"
    )
    process_rows = []
    if PROC_ROOT.exists():
        for entry in PROC_ROOT.iterdir():
            if not entry.name.isdigit():
                continue
            cmdline = proc_cmdline(int(entry.name))
            lowered = cmdline.lower()
            if cmdline and any(keyword in lowered for keyword in process_keywords):
                process_rows.append(f"pid={entry.name} cmd={cmdline!r}")
    lines.append("host_processes_begin")
    lines.extend(process_rows or ["none"])
    lines.append("host_processes_end")

    status, body = docker_request("/containers/json?all=1")
    lines.append(f"docker_list_status={status}")
    if status == 200:
        try:
            containers = json.loads(body.decode("utf-8", "replace"))
        except Exception:
            containers = []
        for item in containers:
            names = [str(x) for x in item.get("Names") or []]
            image = str(item.get("Image") or "")
            state = str(item.get("State") or "")
            labels = item.get("Labels") or {}
            project = str(labels.get("com.docker.compose.project", ""))
            service = str(labels.get("com.docker.compose.service", ""))
            ports = []
            interesting = False
            for port in item.get("Ports") or []:
                private = int(port.get("PrivatePort") or 0)
                public = int(port.get("PublicPort") or 0)
                ip = str(port.get("IP") or "")
                ports.append(f"{ip}:{public}->{private}/{port.get('Type','')}")
                if private in {80, 443, 8000, 18000} or public in {80, 443, 8000, 18000}:
                    interesting = True
            haystack = " ".join(names + [image, project, service]).lower()
            if interesting or any(k in haystack for k in ("nginx", "caddy", "traefik", "django", "mones", "moones")):
                lines.append(
                    f"docker_container names={names} image={image!r} state={state!r} "
                    f"project={project!r} service={service!r} ports={ports}"
                )

    # Host socket table without requiring nsenter: /host/proc/net/* belongs to
    # the host network namespace when /proc is bind-mounted from the host.
    for proto in ("tcp", "tcp6"):
        table = PROC_ROOT / "net" / proto
        if not table.exists():
            continue
        try:
            rows = table.read_text(errors="replace").splitlines()[1:]
        except Exception:
            continue
        for row in rows:
            parts = row.split()
            if len(parts) < 4 or parts[3] != "0A":
                continue
            local = parts[1]
            try:
                _, hex_port = local.split(":")
                port = int(hex_port, 16)
            except Exception:
                continue
            if port in {80, 443, 8000, 18000}:
                lines.append(f"host_listener proto={proto} port={port} raw={local}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print("=== MOONES HOST ROUTING DIAGNOSTIC ===", flush=True)
    print("\n".join(lines), flush=True)
    print("=== END MOONES HOST ROUTING DIAGNOSTIC ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
