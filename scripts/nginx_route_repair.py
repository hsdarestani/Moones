#!/usr/bin/env python3
import http.client
import json
import os
import re
import socket
import struct
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


def decode_docker_logs(data: bytes) -> str:
    # Docker multiplexes non-TTY stdout/stderr as 8-byte framed records.
    out = []
    offset = 0
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        end = offset + 8 + length
        if end > len(data):
            break
        out.append(data[offset + 8:end].decode("utf-8", "replace"))
        offset = end
    if out:
        return "".join(out)
    return data.decode("utf-8", "replace")


def main() -> int:
    lines: list[str] = []
    lines.append("MOONES_HOST_ROUTING_DIAGNOSTIC v3")
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
            returns = [" ".join(item.split()) for item in re.findall(r"\breturn\s+([^;]+);", server_block, flags=re.I)]
            lines.append(
                f"nginx_moones_block file={path} listen={listens} server_name={server_names} "
                f"proxy_pass={proxies} return={returns}"
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

    gateway_id = None
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
            if "/mones-gateway" in names:
                gateway_id = str(item.get("Id") or "")
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

    if gateway_id:
        log_status, log_body = docker_request(
            f"/containers/{gateway_id}/logs?stdout=1&stderr=1&tail=100&timestamps=0"
        )
        lines.append(f"gateway_logs_status={log_status}")
        if log_status == 200:
            gateway_logs = decode_docker_logs(log_body).strip()
            lines.append("gateway_logs_begin")
            lines.extend(gateway_logs.splitlines() or ["empty"])
            lines.append("gateway_logs_end")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print("=== MOONES HOST ROUTING DIAGNOSTIC ===", flush=True)
    print("\n".join(lines), flush=True)
    print("=== END MOONES HOST ROUTING DIAGNOSTIC ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
