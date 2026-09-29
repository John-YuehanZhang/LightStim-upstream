"""Recording HTTP(S) proxy for agent processes.

Every agent process gets its own proxy (HTTP_PROXY/HTTPS_PROXY point to it). The
proxy forwards everything and blocks nothing; it records each destination
(time, method, host, port) to a per-process log so that network use can be
audited after the run (runner/audit_network.py). Programs that open raw sockets
bypass it; the audit also scans the agent transcripts for that.
"""
from __future__ import annotations

import json
import select
import socket
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(65537).decode("latin-1")
            method, target, _ = line.split(" ", 2)
            headers = []
            while True:
                h = self.rfile.readline(65537)
                if h in (b"\r\n", b"\n", b""):
                    break
                headers.append(h)
        except Exception:
            return
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            port = int(port or 443)
        else:
            u = urlsplit(target)
            host, port = u.hostname or "", u.port or 80
        self.server.record(method, host, port)
        try:
            up = socket.create_connection((host, port), timeout=30)
        except Exception:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        if method.upper() == "CONNECT":
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            u = urlsplit(target)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            up.sendall(f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + b"".join(headers) + b"\r\n")
        self._pipe(self.connection, up)

    @staticmethod
    def _pipe(a, b):
        socks = [a, b]
        try:
            while True:
                r, _, x = select.select(socks, [], socks, 600)
                if x or not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except Exception:
            pass
        finally:
            b.close()


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


class RecordingProxy:
    def __init__(self, log_path: Path, label: str):
        self.log_path = Path(log_path)
        self.label = label
        self._lock = threading.Lock()
        self.srv = _Server(("127.0.0.1", 0), _Handler)
        self.srv.record = self.record
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def record(self, method, host, port):
        with self._lock, open(self.log_path, "a") as f:
            f.write(json.dumps({"ts": time.time(), "process": self.label, "method": method, "host": host,
                                "port": port}) + "\n")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict:
        return {"HTTPS_PROXY": self.url, "HTTP_PROXY": self.url, "https_proxy": self.url, "http_proxy": self.url,
                "NO_PROXY": "", "no_proxy": ""}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
