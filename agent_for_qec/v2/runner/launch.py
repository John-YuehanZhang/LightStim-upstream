#!/usr/bin/env python3
"""Launch one role process (main | worker | refuter | novelty) for a project.

  launch.py --project P --role worker --worker w1 [--dry-run]

Builds the English prompt from prompts/<role>.md (+ domain files), acquires a
credential lease from the configured provider and runs the harness headless
inside the agent sandbox (lib/sandbox.wrap_agent):

  * the process sees only system directories, the python environment, the
    harness binary and the repository (without .git, v1 material and other
    projects' results), its own working directory and a fresh home directory;
  * the store is reached only through `qec.py`, which talks over a unix socket
    to the service in the orchestrator; the role is bound to that socket;
  * built-in tools are restricted per role with --tools; only the novelty role
    has WebSearch/WebFetch;
  * the process's own operating rules (e.g. how many subagents it may run, from
    its account's remaining quota) are written to the store as an
    `operating_rules` memory before it starts; the agent reads them with
    `qec.py status`. Nothing about quota or accounts is ever in the prompt;
  * all HTTP(S) traffic goes through a recording proxy (lib/netlog.py); the
    destinations are logged next to the transcript for the network audit.

Stream-json output is logged; the run (prompt SHA-256, repo HEAD, account,
model, cost, turns) is recorded in the store, and every prompt text actually
sent is archived under <runtime>/<project>/prompts_used/ so the paper can cite
exact prompts. Nothing about quota or accounts is ever added to the prompt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
REPO = V2.parents[1]
sys.path.insert(0, str(V2 / "lib"))
from store import Store, RUNTIME_ROOT  # noqa: E402
from providers import load_config, acquire, mark_exhausted, NoCredential  # noqa: E402
from netlog import RecordingProxy  # noqa: E402
import sandbox  # noqa: E402

PY = os.environ.get("QEC_PYTHON", "/home/yuehan/miniconda3/envs/light_stim/bin/python")
QEC_CMD = f"{PY} {V2 / 'qec.py'}"
SOCK_IN_SANDBOX = "/tmp/qec"

BASE_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Task"]
ROLE_TOOLS = {
    "main": BASE_TOOLS,
    "worker": BASE_TOOLS,
    "refuter": BASE_TOOLS,
    "novelty": BASE_TOOLS + ["WebSearch", "WebFetch"],   # the only role with web access
}
# Bash has network access (the harness needs it); these rules keep the obvious
# web clients out of the solving roles. The novelty audit after acceptance is
# the real check that nothing was looked up.
NO_WEB_BASH = ["Bash(curl:*)", "Bash(wget:*)", "Bash(git clone:*)", "Bash(pip:*)", "Bash(pip3:*)",
               "Bash(conda:*)", "Bash(ssh:*)", "Bash(scp:*)", "Bash(nc:*)"]

# parts of the repository that must not be visible to agents: git history
# (contains unrelated branches), v1 material and earlier projects' results
HIDE_IN_REPO = [".git", "agent_for_qec/phase0", "agent_for_qec/phase1", "agent_for_qec/PROGRESS.md",
                "agent_for_qec/LEDGER.md", "agent_for_qec/prompts", "agent_for_qec/runner",
                "agent_for_qec/v2/results", "agent_for_qec/v2/tasks", "agent_for_qec/v2/tests",
                "agent_for_qec/v2/config", "agent_for_qec/v2/runner"]


def build_prompt(role: str, project: str, worker: str, st: Store) -> str:
    tpl = (V2 / "prompts" / f"{role}.md").read_text()
    includes = {
        "{TASK}": Path(st.get("task_path")).read_text() if st.get("task_path") else "(no task)",
        "{ANGLES}": (V2 / "prompts" / "domain" / "angles.md").read_text(),
        "{PITFALLS}": (V2 / "prompts" / "domain" / "pitfalls.md").read_text(),
        "{SUBMISSION_FORMAT}": (V2 / "prompts" / "shared" / "submission_format.md").read_text(),
    }
    for k, v in includes.items():   # included files may themselves use the placeholders below
        tpl = tpl.replace(k, v)
    rep = {
        "{PROJECT}": project, "{ROLE}": role, "{WORKER}": worker, "{ROUND}": str(st.current_round()),
        "{QEC}": QEC_CMD, "{PY}": PY, "{REPO}": str(REPO),
        "{WORKDIR}": str(st.dir / "workers" / worker),
        "{RESULTS}": str(V2 / "results" / project),
    }
    for k, v in rep.items():
        tpl = tpl.replace(k, v)
    return tpl


def harness_binary() -> str:
    import shutil
    b = shutil.which("claude")
    if not b:
        raise RuntimeError("claude binary not found")
    return str(Path(b).resolve())


def sandbox_cmd(inner, *, project, wdir, home, sock_dir):
    pyenv = str(Path(PY).resolve().parents[1])
    results = V2 / "results" / project
    results.mkdir(parents=True, exist_ok=True)
    return sandbox.wrap_agent(
        inner,
        readonly=[pyenv, str(Path(harness_binary()).parent), str(REPO)],
        hide=[str(REPO / h) for h in HIDE_IN_REPO],
        readonly_after=[str(results)],
        writable=[str(wdir), str(home)],
        binds=[(sock_dir, SOCK_IN_SANDBOX)],
        cwd=str(wdir))


def sandbox_env(lease_env: dict, home: Path) -> dict:
    pyenv = str(Path(PY).resolve().parents[1])
    env = {"PATH": f"{pyenv}/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(home),
           "CLAUDE_CONFIG_DIR": str(home / ".claude"), "LANG": os.environ.get("LANG", "C.UTF-8"),
           "TMPDIR": "/tmp", "PYTHONPATH": str(REPO), "QEC_SOCKET": f"{SOCK_IN_SANDBOX}/sock",
           "DISABLE_AUTOUPDATER": "1"}
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        if k in os.environ:
            env[k] = os.environ[k]
    env.update(lease_env)
    return env


def harness_cmd(role: str, model: str, prompt_file: Path, settings_file: Path, turns: int) -> list:
    return [harness_binary(), "-p", prompt_file.read_text(), "--model", model,
            "--tools", ",".join(ROLE_TOOLS[role]), "--setting-sources", "user", "--settings", str(settings_file),
            "--strict-mcp-config", "--permission-mode", "dontAsk", "--max-turns", str(turns),
            "--output-format", "stream-json", "--verbose"]


def subagent_limit(headroom: float, ocfg: dict) -> int:
    """Operator policy: subagents per process from the account's remaining quota
    (parallelism changes speed, not results)."""
    for thr, n in ocfg.get("subagent_thresholds", [[0.5, 2], [0.2, 1]]):
        if headroom >= thr:
            return int(n)
    return 0


def process_rules(n_sub: int) -> str:
    return ("- You may run at most {n} subagent(s) at a time.{x}".format(
        n=n_sub, x=" Do all work yourself, sequentially." if n_sub == 0 else ""))


def role_settings(role: str) -> dict:
    deny = [] if role == "novelty" else list(NO_WEB_BASH)
    return {"permissions": {"allow": list(ROLE_TOOLS[role]), "deny": deny, "defaultMode": "dontAsk"},
            "includeCoAuthoredBy": False}


class CpuSlot:
    """Exclusive block of CPU cores for one agent process (operator limit from
    [orchestrator] cpus_per_agent / cpu_first / cpu_slots; enforced with taskset,
    so an agent's parallel jobs cannot exceed it)."""

    def __init__(self, ocfg: dict):
        import fcntl
        per, first, n = (int(ocfg.get("cpus_per_agent", 16)), int(ocfg.get("cpu_first", 0)),
                         int(ocfg.get("cpu_slots", 8)))
        d = RUNTIME_ROOT / "cpuslots"
        d.mkdir(parents=True, exist_ok=True)
        self.fh, self.cpus = None, None
        for i in range(n):
            fh = open(d / f"{i}.lock", "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.close()
                continue
            self.fh, self.cpus = fh, f"{first + i * per}-{first + (i + 1) * per - 1}"
            return
        raise NoCredential("no free CPU slot")

    def release(self):
        if self.fh is not None:
            self.fh.close()
            self.fh = None


def parse_log(log: Path) -> dict:
    """Final result line plus any rejected rate-limit event."""
    res, rejected = {}, None
    if log.exists():
        for line in log.read_text(errors="ignore").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "result":
                res = d
            elif d.get("type") == "rate_limit_event":
                info = d.get("rate_limit_info") or {}
                if info.get("status") == "rejected":
                    rejected = info
    return {"result": res, "rejected": rejected}


def _kill_group(p: subprocess.Popen):
    for sig, wait in ((signal.SIGTERM, 30), (signal.SIGKILL, 10)):
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run(project: str, role: str, worker: str, dry_run: bool = False, config_path: str = None,
        max_turns: int = None, service=None) -> dict:
    """Run one agent process. `service` is the orchestrator's lib.service.Service;
    when None (stand-alone use) a private one is started for this process."""
    cfg = load_config(config_path)
    rcfg = cfg["roles"][role]
    st = Store(project)
    wdir = st.dir / "workers" / worker
    wdir.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(role, project, worker, st)
    psha = hashlib.sha256(prompt.encode()).hexdigest()
    pdir = st.dir / "prompts_used"
    pdir.mkdir(exist_ok=True)
    pfile = pdir / f"{psha[:16]}_{role}.md"
    pfile.write_text(prompt)
    turns = max_turns or rcfg.get("max_turns", 100)
    ts = time.strftime("%Y%m%d_%H%M%S")
    home = st.dir / "homes" / f"{ts}_{role}_{worker}_{uuid.uuid4().hex[:6]}"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    settings_file = home / ".claude" / "settings.json"
    settings_file.write_text(json.dumps(role_settings(role), indent=1))
    inner = harness_cmd(role, rcfg["model"], pfile, settings_file, turns)

    if dry_run:
        cmd = sandbox_cmd(inner, project=project, wdir=wdir, home=home, sock_dir="<socket dir>")
        i = cmd.index("-p")
        cmd[i + 1] = f"<prompt {len(prompt)} chars sha {psha[:12]}>"
        return {"cmd": cmd, "prompt_file": str(pfile), "settings": role_settings(role)}

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    slot = CpuSlot(cfg.get("orchestrator", {}))
    try:
        lease = acquire(rcfg, cfg, RUNTIME_ROOT)
    except Exception:
        slot.release()
        raise
    own_service = service is None
    if own_service:
        from service import Service
        service = Service(project, RUNTIME_ROOT)
    logdir = st.dir / "logs"
    logdir.mkdir(exist_ok=True)
    log = logdir / f"{ts}_r{st.current_round()}_{role}_{worker}_{lease.account}.jsonl"
    run_id = st.add_run(round=st.current_round(), role=role, worker=worker, account=lease.account,
                        provider=lease.provider, model=rcfg["model"], prompt_sha=psha, repo_head=head,
                        log=str(log), started=time.time())
    sock_dir = service.endpoint(role, worker, [str(wdir)], run_id=run_id)
    st.set_operating_rules(worker, process_rules(subagent_limit(lease.headroom, cfg.get("orchestrator", {}))))
    cmd = ["taskset", "-c", slot.cpus] + sandbox_cmd(inner, project=project, wdir=wdir, home=home,
                                                      sock_dir=sock_dir)
    proxy = RecordingProxy(log.with_suffix(".net.log"), f"{role}/{worker}")
    env = {**sandbox_env(lease.env, home), **proxy.env()}
    try:
        with open(log, "w") as out, open(log.with_suffix(".err"), "w") as err:
            p = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 start_new_session=True)
            try:
                rc = p.wait(timeout=rcfg.get("wall_hours", 4.5) * 3600)
            except subprocess.TimeoutExpired:
                _kill_group(p)
                rc = -9
    finally:
        lease.release()
        slot.release()
        proxy.close()
        service.close(sock_dir)
        if own_service:
            service.shutdown()
    parsed = parse_log(log)
    res, rej = parsed["result"], parsed["rejected"]
    cost = res.get("total_cost_usd") or 0
    u = res.get("usage") or {}
    st.finish_run(run_id, ended=time.time(), exit=rc, cost_usd=cost, turns=res.get("num_turns"),
                  result_head=str(res.get("result", ""))[:300], input_tokens=u.get("input_tokens"),
                  output_tokens=u.get("output_tokens"), cache_read_tokens=u.get("cache_read_input_tokens"),
                  cache_creation_tokens=u.get("cache_creation_input_tokens"))
    with open(RUNTIME_ROOT / "usage_log.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.time(), "account": lease.account, "model": rcfg["model"], "role": role,
                            "project": project, "cost_usd": cost, "exit": rc}) + "\n")
    text = str(res.get("result", "")).lower()
    hit_limit = rej is not None or (bool(res.get("is_error")) and ("usage limit" in text or "rate limit" in text))
    if hit_limit and lease.provider == "claude_oauth":
        mark_exhausted(RUNTIME_ROOT, lease.account, rcfg["model"], resets_at=(rej or {}).get("resetsAt"),
                       why=f"rejected: {(rej or {}).get('rateLimitType', 'unknown')}")
    return {"exit": rc, "account": lease.account, "log": str(log), "cost_usd": cost, "turns": res.get("num_turns"),
            "hit_limit": hit_limit, "limit_type": (rej or {}).get("rateLimitType"),
            "is_error": bool(res.get("is_error")) or not res, "result": str(res.get("result", ""))[:500]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--role", required=True, choices=list(ROLE_TOOLS))
    ap.add_argument("--worker", default=None)
    ap.add_argument("--config")
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        out = run(a.project, a.role, a.worker or a.role, a.dry_run, a.config, a.max_turns)
    except NoCredential as e:
        print(json.dumps({"exit": 3, "error": str(e)}))
        sys.exit(3)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
