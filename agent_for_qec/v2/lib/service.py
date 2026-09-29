"""The shared-state service (runs inside the orchestrator process).

Each launched agent gets its own unix socket; the role, worker name and the
directories the agent may reference are bound to the socket when it is
created, so an agent cannot act under another role. The store and the gate run
here, outside every agent sandbox.

Submissions run on a background thread; the caller waits up to `--wait`
seconds (default 540, below the 10-minute tool-call limit) and otherwise gets
the submission id to query later with `qec.py submission ID`.
"""
from __future__ import annotations

import json
import os
import socketserver
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
V2 = HERE.parent
sys.path.insert(0, str(V2))
sys.path.insert(0, str(HERE))
import qec  # noqa: E402

_DISPATCH_LOCK = threading.Lock()   # stdout capture in qec.dispatch is process-global


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        ep = self.server.endpoint
        try:
            req = json.loads(self.rfile.readline().decode())
            argv = [str(x) for x in req.get("argv", [])]
            cwd = str(req.get("cwd") or ep["roots"][0])
        except Exception as ex:
            self._send(f"bad request: {ex}\n", 2)
            return
        svc = self.server.service
        box = {}

        def hook(a):          # submit: only parse + permission check happen under the lock
            box["a"] = a
            return None

        with _DISPATCH_LOCK:
            out, code = qec.dispatch(argv, svc.project, ep["role"], ep["worker"], submit_hook=hook,
                                     cwd=cwd, allowed_roots=ep["roots"])
        if "a" in box and code == 0:
            out = svc.submit(box["a"], ep)
        self._send(out, code)

    def _send(self, out, code):
        self.wfile.write(json.dumps({"out": out, "code": code}).encode())


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Service:
    def __init__(self, project: str, runtime: Path):
        self.project = project
        import tempfile
        # unix socket paths are limited to ~108 bytes, so sockets live under a short /tmp path
        self.sock_root = Path(tempfile.mkdtemp(prefix="qecs_", dir="/tmp"))
        self.servers: Dict[str, _Server] = {}
        self.jobs: Dict[str, dict] = {}

    def endpoint(self, role: str, worker: str, roots: List[str], run_id=None) -> str:
        """Create a socket for one agent process; returns the directory containing `sock`."""
        eid = f"{role}_{worker}_{uuid.uuid4().hex[:8]}"
        d = self.sock_root / eid
        d.mkdir(parents=True)
        path = d / "sock"
        srv = _Server(str(path), _Handler)
        srv.endpoint = {"role": role, "worker": worker, "roots": [str(Path(r).resolve()) for r in roots],
                        "run_id": run_id}
        srv.service = self
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers[eid] = srv
        return str(d)

    def close(self, sock_dir: str) -> None:
        eid = Path(sock_dir).name
        srv = self.servers.pop(eid, None)
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        try:
            (Path(sock_dir) / "sock").unlink()
            Path(sock_dir).rmdir()
        except OSError:
            pass

    def submit(self, a, ep) -> str:
        from store import Store
        from gate import run_gate, bundle_hash
        sub = Path(a.dir)
        try:
            sid = bundle_hash(sub)[:12]
        except Exception as ex:
            return f"cannot read submission directory: {ex}"
        done = threading.Event()
        box = {}

        def job():
            try:
                box["rep"] = run_gate(sub, Store(self.project), ep["worker"], milp_time_s=a.milp_time,
                                      run_id=ep.get("run_id"))
            except Exception as ex:  # run_gate records its own failures; this is a last resort
                box["rep"] = {"outcome": "error", "reason": f"gate crashed: {type(ex).__name__}: {ex}"}
            done.set()

        threading.Thread(target=job, daemon=True).start()
        if done.wait(timeout=max(1.0, min(a.wait, 540.0))):
            import io
            buf = io.StringIO()
            qec.print_report(box["rep"], file=buf)
            return buf.getvalue()
        return (f"submission {sid} is still being verified (gate running in the background).\n"
                f"Query it later with: qec.py submission {sid}\n"
                f"Do not end your turn before you have read its outcome.")

    def shutdown(self):
        import shutil
        for eid in list(self.servers):
            srv = self.servers.pop(eid)
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(self.sock_root, ignore_errors=True)
