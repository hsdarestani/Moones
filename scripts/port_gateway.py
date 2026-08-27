#!/usr/bin/env python3
import asyncio
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import time
from urllib.parse import quote

UPSTREAM_HOST = os.getenv("MOONES_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.getenv("MOONES_UPSTREAM_PORT", "18000"))
DOCKER_SOCKET = "/var/run/docker.sock"

# Never replace public reverse proxies, SSH, databases, or the new FastAPI port.
PROTECTED_PORTS = {22, 25, 53, 80, 110, 143, 443, 465, 587, 993, 995, 3306, 5432, 6379, 18000}
PROXY_MARKERS = ("nginx", "caddy", "traefik", "apache", "httpd", "haproxy")
DJANGO_MARKERS = ("config.urls", "tg/webhook/", "automation/", "safety/", "page not found")


def log(message: str) -> None:
    print(f"MOONES_GATEWAY {message}", flush=True)


def docker_request(method: str, path: str) -> tuple[int, bytes]:
    if not os.path.exists(DOCKER_SOCKET):
        return 0, b""

    class UnixHTTPConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect(DOCKER_SOCKET)

    conn = UnixHTTPConnection("localhost", timeout=5)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        return response.status, response.read()
    except Exception as exc:
        log(f"docker_request_failed method={method} path={path} error={exc!r}")
        return 0, b""
    finally:
        try:
            conn.close()
        except Exception:
            pass


def listening_ports() -> list[int]:
    try:
        proc = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, timeout=5, check=False)
    except Exception as exc:
        log(f"ss_failed error={exc!r}")
        return []

    ports: set[int] = set()
    for line in proc.stdout.splitlines():
        if not line.startswith("LISTEN"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        match = re.search(r":(\d+)$", local)
        if not match:
            continue
        port = int(match.group(1))
        if port < 1024 or port in PROTECTED_PORTS:
            continue
        ports.add(port)
    return sorted(ports)


def has_legacy_signature(port: int) -> bool:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.5)
    try:
        conn.request(
            "GET",
            "/static/pitch.html",
            headers={
                "Host": "moones.top",
                "User-Agent": "MoonesBackendRecovery/2.0",
                "Connection": "close",
            },
        )
        response = conn.getresponse()
        body = response.read(250000).decode("utf-8", "replace").lower()
        matched = response.status == 404 and all(marker in body for marker in DJANGO_MARKERS)
        if matched:
            log(f"stale_django_signature_found port={port}")
        return matched
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def docker_container_for_port(port: int) -> dict | None:
    status, body = docker_request("GET", "/containers/json?all=1")
    if status != 200:
        return None
    try:
        containers = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None

    for container in containers:
        for mapping in container.get("Ports") or []:
            if int(mapping.get("PublicPort") or 0) == port:
                return container
    return None


def listener_pid(port: int) -> tuple[int | None, str]:
    try:
        proc = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return None, ""
    match = re.search(r'users:\(\("([^\"]+)"[^)]*pid=(\d+)', proc.stdout)
    if not match:
        match = re.search(r"pid=(\d+)", proc.stdout)
        if not match:
            return None, proc.stdout.strip()
        return int(match.group(1)), proc.stdout.strip()
    return int(match.group(2)), match.group(1)


def port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def stop_stale_backend(port: int) -> bool:
    container = docker_container_for_port(port)
    if container is not None:
        names = " ".join(container.get("Names") or []).lower()
        image = str(container.get("Image") or "").lower()
        labels = container.get("Labels") or {}
        project = str(labels.get("com.docker.compose.project", "")).lower()
        service = str(labels.get("com.docker.compose.service", "")).lower()
        identity = " ".join((names, image, project, service))
        if any(marker in identity for marker in PROXY_MARKERS):
            log(f"refusing_to_stop_proxy_container port={port} names={names!r} image={image!r}")
            return False
        container_id = str(container.get("Id") or "")
        if not container_id:
            return False
        log(f"stopping_exact_stale_container port={port} id={container_id[:12]} names={names!r} image={image!r}")
        status, _ = docker_request("POST", f"/containers/{quote(container_id)}/stop?t=2")
        if status not in {204, 304}:
            log(f"docker_stop_failed port={port} status={status}")
            return False
    else:
        pid, process_name = listener_pid(port)
        if not pid:
            log(f"stale_port_owner_unknown port={port}")
            return False
        identity = process_name.lower()
        try:
            cmdline = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception:
            cmdline = process_name
        identity += " " + cmdline.lower()
        if any(marker in identity for marker in PROXY_MARKERS):
            log(f"refusing_to_stop_proxy_process port={port} pid={pid} cmd={cmdline!r}")
            return False
        if pid == os.getpid():
            return False
        log(f"stopping_exact_stale_process port={port} pid={pid} cmd={cmdline!r}")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            log(f"process_stop_failed port={port} pid={pid} error={exc!r}")
            return False

    for _ in range(30):
        if port_is_free(port):
            log(f"stale_backend_port_reclaimed port={port}")
            return True
        time.sleep(0.1)
    log(f"stale_backend_port_still_busy port={port}")
    return False


def discover_stale_backend_port() -> int | None:
    candidates = listening_ports()
    log(f"candidate_internal_ports={candidates}")
    for port in candidates:
        if has_legacy_signature(port):
            return port
    return None


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT), timeout=5
        )
    except Exception as exc:
        log(f"upstream_connect_failed upstream={UPSTREAM_HOST}:{UPSTREAM_PORT} error={exc!r}")
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except Exception:
            pass
        return

    left = asyncio.create_task(relay(client_reader, upstream_writer))
    right = asyncio.create_task(relay(upstream_reader, client_writer))
    done, pending = await asyncio.wait({left, right}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, *pending, return_exceptions=True)


async def main() -> None:
    stale_port = discover_stale_backend_port()
    if stale_port is None:
        log("no_exact_stale_django_backend_found; safe_standby")
        while True:
            await asyncio.sleep(3600)

    if not stop_stale_backend(stale_port):
        log(f"unable_to_safely_reclaim_stale_backend port={stale_port}; safe_standby")
        while True:
            await asyncio.sleep(3600)

    server = await asyncio.start_server(
        handle_client,
        "127.0.0.1",
        stale_port,
        reuse_address=True,
    )
    log(f"recovery_gateway_started listen=127.0.0.1:{stale_port} upstream={UPSTREAM_HOST}:{UPSTREAM_PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
