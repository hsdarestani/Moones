"""Read-only release diagnostics. Never output secret hosts or credentials."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import socket
import urllib.error
import urllib.request


def probe(target):
    name, host, port = target
    try:
        with socket.create_connection((host, port), timeout=6):
            return {"target": name, "port": port, "reachable": True}
    except OSError as exc:
        return {"target": name, "port": port, "reachable": False, "error_type": type(exc).__name__}


def main():
    names = ["PROD_HOST", "PROD_PORT", "PROD_USER", "PROD_SSH_PRIVATE_KEY", "PROD_SSH_KNOWN_HOSTS",
             "HOST", "PASS", "SSH_PORT", "SHARED_VPS_PASS", "SERVER_IP", "SERVER_USER", "SERVER_PASSWORD",
             "VENICE_API_KEY", "VENICE_API_KEY_STAGING"]
    report = {"configured": {name: bool(os.getenv(name, "").strip()) for name in names}}
    hosts = {name: os.getenv(name, "").strip() for name in ["PROD_HOST", "HOST", "SERVER_IP"]}
    hosts["public_domain"] = "moones.top"
    try:
        public_ips = {row[4][0] for row in socket.getaddrinfo("moones.top", None, type=socket.SOCK_STREAM)}
        report["public_domain_ips"] = sorted(public_ips)
    except OSError:
        public_ips = set()
        report["public_domain_dns_failed"] = True
    report["routing_matches_domain"] = {}
    targets = []
    for name, host in hosts.items():
        if not host:
            continue
        try:
            resolved = {row[4][0] for row in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
            report["routing_matches_domain"][name] = bool(resolved & public_ips)
        except OSError:
            report["routing_matches_domain"][name] = False
        configured_port = os.getenv("PROD_PORT" if name == "PROD_HOST" else "SSH_PORT", "")
        ports = {22, 9011}
        if configured_port.isdigit() and 0 < int(configured_port) < 65536:
            ports.add(int(configured_port))
        targets.extend((name, host, port) for port in sorted(ports))
    with ThreadPoolExecutor(max_workers=8) as executor:
        report["tcp"] = list(executor.map(probe, targets))
    try:
        with urllib.request.urlopen("https://moones.top/health", timeout=15) as response:
            report["health"] = {"http_status": response.status,
                                "status_ok": json.loads(response.read(2000)).get("status") == "ok"}
    except urllib.error.HTTPError as exc:
        report["health"] = {"http_status": exc.code, "status_ok": False}
    except Exception as exc:
        report["health"] = {"status_ok": False, "error_type": type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
