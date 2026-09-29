#!/usr/bin/env python3
"""Launch one role process (main | worker | reviewer | novelty) for a project.

  launch.py --project P --role worker --worker w1 [--dry-run]

Builds the English prompt from prompts/<role>.md (+ domain files), acquires a
credential lease from the configured provider, runs the harness headless with a
per-role tool allowlist, logs stream-json output, and records the run (prompt
SHA-256, repo HEAD, account, model, cost, turns) in the store. Every prompt text
actually sent is archived under <runtime>/<project>/prompts_used/<sha>.md so the
paper can cite exact prompts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
REPO = V2.parents[1]
sys.path.insert(0, str(V2 / "lib"))
from store import Store, RUNTIME_ROOT  # noqa: E402
from providers import load_config, acquire, mark_exhausted, NoCredential  # noqa: E402

PY = os.environ.get("QEC_PYTHON", "/home/yuehan/miniconda3/envs/light_stim/bin/python")
QEC_CMD = f"{PY} agent_for_qec/v2/qec.py"

READ_TOOLS = ["Read", "Glob", "Grep", "TodoWrite", "LS"]
EDIT_TOOLS = ["Write", "Edit", "MultiEdit"]
SHELL_READ = ["Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(grep:*)",
              "Bash(find:*)", "Bash(sed -n:*)", "Bash(diff:*)", "Bash(echo:*)", "Bash(date:*)",
              "Bash(git status:*)", "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)"]
SHELL_QEC = [f"Bash({QEC_CMD}:*)"]
SHELL_PY = [f"Bash({PY}:*)", "Bash(PYTHONPATH=:*)", "Bash(timeout:*)", "Bash(nohup:*)", "Bash(mkdir:*)",
            "Bash(cp:*)", "Bash(mv:*)", "Bash(sleep:*)", "Bash(until:*)"]
WEB = ["WebSearch", "WebFetch"]
SUBAGENT = ["Task", "Agent"]

ROLE_TOOLS = {
    # main: reads everything, runs small computations, writes strategy through qec.py; no web while solving
    "main": READ_TOOLS + EDIT_TOOLS + SHELL_READ + SHELL_QEC + SHELL_PY + SUBAGENT,
    # worker: builds and tests candidates, submits through the gate; no web while solving
    "worker": READ_TOOLS + EDIT_TOOLS + SHELL_READ + SHELL_QEC + SHELL_PY + SUBAGENT,
    # reviewer: reads and runs checks, records a review; no web
    "reviewer": READ_TOOLS + EDIT_TOOLS + SHELL_READ + SHELL_QEC + SHELL_PY,
    # novelty: the only role with web access; runs after the gate has accepted a fact
    "novelty": READ_TOOLS + EDIT_TOOLS + SHELL_READ + SHELL_QEC + WEB,
}
DENY = ["Bash(git push:*)", "Bash(git commit:*)", "Bash(rm -rf:*)", "Bash(sudo:*)", "Bash(pip install:*)",
        "Bash(curl:*)", "Bash(wget:*)"]


def build_prompt(role: str, project: str, worker: str, st: Store) -> str:
    tpl = (V2 / "prompts" / f"{role}.md").read_text()
    rep = {
        "{PROJECT}": project, "{ROLE}": role, "{WORKER}": worker, "{ROUND}": str(st.current_round()),
        "{QEC}": QEC_CMD, "{PY}": PY, "{REPO}": str(REPO),
        "{WORKDIR}": str(st.dir / "workers" / worker),
        "{TASK}": Path(st.get("task_path")).read_text() if st.get("task_path") else "(no task)",
        "{ANGLES}": (V2 / "prompts" / "domain" / "angles.md").read_text(),
        "{PITFALLS}": (V2 / "prompts" / "domain" / "pitfalls.md").read_text(),
        "{SUBMISSION_FORMAT}": (V2 / "prompts" / "shared" / "submission_format.md").read_text(),
    }
    for k, v in rep.items():
        tpl = tpl.replace(k, v)
    return tpl


def parse_result(log: Path) -> dict:
    res = {}
    if log.exists():
        for line in log.read_text(errors="ignore").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") == "result":
                res = d
    return res


def run(project: str, role: str, worker: str, dry_run: bool = False, config_path: str = None,
        max_turns: int = None) -> dict:
    cfg = load_config(config_path)
    rcfg = cfg["roles"][role]
    st = Store(project)
    wdir = st.dir / "workers" / worker
    wdir.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(role, project, worker, st)
    psha = hashlib.sha256(prompt.encode()).hexdigest()
    pdir = st.dir / "prompts_used"
    pdir.mkdir(exist_ok=True)
    (pdir / f"{psha[:16]}_{role}.md").write_text(prompt)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    turns = max_turns or rcfg.get("max_turns", 100)
    cmd = ["claude", "-p", prompt, "--model", rcfg["model"], "--permission-mode", "acceptEdits",
           "--allowedTools", *ROLE_TOOLS[role], "--disallowedTools", *DENY,
           "--add-dir", str(st.dir), "--max-turns", str(turns), "--output-format", "stream-json", "--verbose"]
    if dry_run:
        return {"cmd": cmd[:2] + ["<prompt %d chars sha %s>" % (len(prompt), psha[:12])] + cmd[3:],
                "prompt_file": str(pdir / f"{psha[:16]}_{role}.md")}
    lease = acquire(rcfg, cfg, RUNTIME_ROOT)
    ts = time.strftime("%Y%m%d_%H%M%S")
    logdir = st.dir / "logs"
    logdir.mkdir(exist_ok=True)
    log = logdir / f"{ts}_r{st.current_round()}_{role}_{worker}_{lease.account}.jsonl"
    run_id = st.add_run(round=st.current_round(), role=role, worker=worker, account=lease.account,
                        provider=lease.provider, model=rcfg["model"], prompt_sha=psha, repo_head=head,
                        log=str(log), started=time.time())
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")}
    env.update(lease.env)
    env.update({"QEC_ROLE": role, "QEC_PROJECT": project, "QEC_WORKER": worker,
                "QEC_RUNTIME_ROOT": str(RUNTIME_ROOT), "PYTHONPATH": str(REPO)})
    try:
        with open(log, "w") as out, open(log.with_suffix(".err"), "w") as err:
            p = subprocess.run(cmd, cwd=REPO, env=env, stdout=out, stderr=err,
                               timeout=rcfg.get("wall_hours", 4.5) * 3600)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = -9
    finally:
        lease.release()
    res = parse_result(log)
    cost = res.get("total_cost_usd") or 0
    st.finish_run(run_id, ended=time.time(), exit=rc, cost_usd=cost, turns=res.get("num_turns"),
                  result_head=str(res.get("result", ""))[:300])
    with open(RUNTIME_ROOT / "usage_log.jsonl", "a") as f:
        f.write(json.dumps({"ts": time.time(), "account": lease.account, "model": rcfg["model"], "role": role,
                            "project": project, "cost_usd": cost, "exit": rc}) + "\n")
    if res.get("is_error") and "limit" in str(res.get("result", "")).lower() and lease.provider == "claude_oauth":
        mark_exhausted(RUNTIME_ROOT, lease.account, rcfg["model"])
    return {"exit": rc, "account": lease.account, "log": str(log), "cost_usd": cost, "turns": res.get("num_turns"),
            "result": str(res.get("result", ""))[:500]}


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
