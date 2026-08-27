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
from pathlib import Path
from urllib.parse import quote

LISTEN_HOST = os.getenv("MOONES_GATEWAY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("MOONES_GATEWAY_PORT", "8000"))
UPSTREAM_HOST = os.getenv("MOONES_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.getenv("MOONES_UPSTREAM_PORT", "8001"))
DOCKER_SOCKET = "/var/run/docker.sock"


def log(message: str) -> None:
    print(f"MOONES_GATEWAY {message}", flush=True)


def port_is_free() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LISTEN_HOST, LISTEN_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def legacy_django_signature() -> bool:
    """Recognize the exact stale Django app currently exposed as moones.top.

    This is deliberately strict so the recovery code cannot stop an unrelated
    Django service merely because it happens to use the same port.
    """
    conn = http.client.HTTPConnection(LISTEN_HOST, LISTEN_PORT, timeout=2)
    try:
        conn.request(
            "GET",
            "/static/pitch.html",
            headers={
                "Host": "moones.top",
                "User-Agent": "MoonesGatewayRecovery/1.0",
                "Connection": "close",
            },
        )
        response = conn.getresponse()
        body = response.read(200000).decode("utf-8", "replace").lower()
    except Exception as exc:
        log(f"legacy_probe_failed error={exc!r}")
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

    markers = (
        "config.urls",
        "tg/webhook/",
        "automation/",
        "safety/",
        "page not found",
    )
    matched = response.status == 404 and all(marker in body for marker in markers)
    if matched:
        log("recognized_legacy_django_signature host=moones.top port=8000")
    return matched


def listener_pid() -> int | None:
    try:
        proc = subprocess.run(
            ["ss", "-ltnp", f"sport = :{LISTEN_PORT}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        match = re.search(r"pid=(\d+)", proc.stdout)
        return int(match.group(1)) if match else None
    except Exception as exc:
        log(f"listener_lookup_failed error={exc!r}")
        return None


def proc_text(pid: int, name: str) -> str:
    try:
        path = Path(f"/proc/{pid}/{name}")
        if name == "cmdline":
            return path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        return path.read_text(errors="replace").strip()
    except Exception:
        return ""


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
    finally:
        conn.close()


def stop_recognized_docker_listener() -> bool:
    try:
        status, body = docker_request("GET", "/containers/json?all=1")
        if status != 200:
            return False
        containers = json.loads(body.decode("utf-8"))
    except Exception as exc:
        log(f"docker_list_failed error={exc!r}")
        return False

    for container in containers:
        ports = container.get("Ports") or []
        owns_port = any(
            int(port.get("PublicPort") or 0) == LISTEN_PORT
            and (port.get("IP") in {None, "", "127.0.0.1", "0.0.0.0", "::"})
            for port in ports
        )
        if not owns_port:
            continue

        names = " ".join(container.get("Names") or []).lower()
        labels = container.get("Labels") or {}
        compose_project = str(labels.get("com.docker.compose.project", "")).lower()
        compose_service = str(labels.get("com.docker.compose.service", "")).lower()
        image = str(container.get("Image", "")).lower()
        recognized = (
            "moones" in names
            or "mones" in names
            or "moones" in compose_project
            or "mones" in compose_project
            or ((compose_service == "app") and ("moones" in image or "mones" in image))
        )
        if not recognized and legacy_django_signature():
            recognized = True
            log(
                "legacy_django_is_docker_listener "
                f"names={names!r} project={compose_project!r} service={compose_service!r} image={image!r}"
            )

        if not recognized:
            log(
                "port_owner_unrecognized_docker "
                f"names={names!r} project={compose_project!r} service={compose_service!r} image={image!r}"
            )
            return False

        container_id = str(container.get("Id", ""))
        if not container_id:
            return False
        log(f"stopping_legacy_container id={container_id[:12]} names={names!r}")
        status, _ = docker_request("POST", f"/containers/{quote(container_id)}/stop?t=3")
        if status not in {204, 304}:
            log(f"docker_stop_failed status={status}")
            return False
        return True
    return False


def stop_recognized_process() -> bool:
    pid = listener_pid()
    if not pid:
        return False
    cmdline = proc_text(pid, "cmdline")
    cgroup = proc_text(pid, "cgroup")
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except Exception:
        cwd = ""

    combined = f"{cmdline} {cwd} {cgroup}".lower()
    recognized = (
        ("uvicorn" in combined or "gunicorn" in combined)
        and ("app.main:app" in combined or "moones" in combined or "mones" in combined)
    ) or bool(re.search(r"(?:^|[/_.-])(moones|mones)(?:[/_.-]|$)", combined))

    if not recognized and legacy_django_signature():
        recognized = True
        log(f"legacy_django_is_host_process pid={pid} cmd={cmdline!r} cwd={cwd!r} cgroup={cgroup!r}")

    if not recognized:
        log(f"port_owner_unrecognized_process pid={pid} cmd={cmdline!r} cwd={cwd!r}")
        return False

    log(f"stopping_legacy_process pid={pid} cmd={cmdline!r}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception as exc:
        log(f"process_term_failed pid={pid} error={exc!r}")
        return False

    for _ in range(10):
        if port_is_free():
            return True
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception as exc:
        log(f"process_kill_failed pid={pid} error={exc!r}")
        return False
    return True


def reclaim_listen_port() -> None:
    for attempt in range(1, 31):
        if port_is_free():
            log(f"listen_port_ready port={LISTEN_PORT} attempt={attempt}")
            return

        recovered = stop_recognized_docker_listener()
        if not recovered:
            recovered = stop_recognized_process()

        if not recovered:
            log(f"listen_port_busy_unknown port={LISTEN_PORT} attempt={attempt}; refusing unsafe kill")
        time.sleep(1)

    raise RuntimeError(f"Unable to safely reclaim {LISTEN_HOST}:{LISTEN_PORT}")


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
    reclaim_listen_port()
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT, reuse_address=True)
    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    log(f"started listen={sockets} upstream={UPSTREAM_HOST}:{UPSTREAM_PORT}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
