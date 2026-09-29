"""Run archive: every project records which system version produced it, and the
runtime root keeps an index of all projects, so results of different versions
are never confused.

  <runtime>/<project>/RUN_INFO.md   written at init, rewritten by `qec.py runinfo`
                                    and at the end of every orchestrator run
  <runtime>/RUNS.md                 one row per project (all projects in the root)

Run status: open (default while running) | valid | historical | void, with a note
saying why. Only `valid` runs may be used in the paper.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
V2 = HERE.parent
REPO = V2.parents[1]


def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout.strip()


def record_init(st) -> None:
    from store import SYSTEM_VERSION
    st.set("system_version", SYSTEM_VERSION)
    st.set("repo_head", _git("rev-parse", "HEAD"))
    st.set("repo_branch", _git("rev-parse", "--abbrev-ref", "HEAD"))
    st.set("repo_dirty", bool(_git("status", "--porcelain", "--", "agent_for_qec/v2", "agent_for_qec/tools",
                                   "lightstim")))
    st.set("initialised", time.time())
    st.set("run_status", "open")
    tp = st.get("task_path")
    if tp:
        st.set("task_sha", hashlib.sha256(Path(tp).read_bytes()).hexdigest())
    cfg = V2 / "config" / "models.toml"
    if cfg.exists():
        st.set("config_snapshot", cfg.read_text())


def _t(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"


def write_run_info(st) -> Path:
    g = st.get
    runs = st.con.execute("SELECT role, model, prompt_sha, COUNT(*) n, SUM(cost_usd) c, MIN(started) s, MAX(ended) e "
                          "FROM runs GROUP BY role, model, prompt_sha ORDER BY s").fetchall()
    facts = st.facts(None)
    by = {}
    for f in facts:
        key = "revoked" if f["status"] == "revoked" else f["refute_status"]
        by[key] = by.get(key, 0) + 1
    subs = dict(st.con.execute("SELECT outcome, COUNT(*) FROM submissions GROUP BY outcome").fetchall())
    results = V2 / "results" / st.project
    L = [f"# RUN_INFO — {st.project}", "",
         f"- run status: **{g('run_status', 'open')}**" + (f" — {g('run_note')}" if g("run_note") else ""),
         f"- system version: v{g('system_version', '?')}  commit `{str(g('repo_head', '?'))[:10]}` "
         f"({g('repo_branch', '?')}){'  **uncommitted changes at init**' if g('repo_dirty') else ''}",
         f"- initialised: {_t(g('initialised'))}; rounds so far: {st.current_round()}; project status: {g('status')}",
         f"- origin: {g('origin')}; noise p = {g('noise_p')}",
         f"- task: `{g('task_path')}` (sha256 {str(g('task_sha', ''))[:12]})",
         "", "## Processes (role, model, prompt sha, count, cost)", ""]
    for r in runs:
        L.append(f"- {r['role']} / {r['model']} / `{str(r['prompt_sha'])[:12]}` × {r['n']}, "
                 f"${(r['c'] or 0):.2f}, {_t(r['s'])} – {_t(r['e'])}")
    L += ["", "## Results", "",
          f"- facts: {len(facts)} ({', '.join(f'{k} {v}' for k, v in sorted(by.items())) or 'none'})",
          f"- submissions: {', '.join(f'{k} {v}' for k, v in sorted(subs.items())) or 'none'}",
          "", "## Where things are", "",
          f"- store: `{st.dir}/store.sqlite`",
          f"- prompts actually sent: `{st.dir}/prompts_used/`",
          f"- process logs: `{st.dir}/logs/` (network logs `*.net.log`)",
          f"- fact bundles and verdicts: `{results}/facts/`",
          f"- ledger: `{results}/LEDGER.md`; network audit: `{results}/NETWORK_AUDIT.md`",
          f"- config at init: stored in the project key `config_snapshot`", ""]
    out = st.dir / "RUN_INFO.md"
    out.write_text("\n".join(L))
    return out


def write_runs_index(root: Path = None) -> Path:
    from store import RUNTIME_ROOT
    root = Path(root or RUNTIME_ROOT)
    rows = []
    for db in sorted(root.glob("*/store.sqlite")):
        try:
            con = sqlite3.connect(db, timeout=30)
            kv = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM project").fetchall()}
            nf = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            con.close()
        except Exception:
            continue
        rows.append((kv.get("initialised") or 0, db.parent.name, kv, nf))
    L = ["# Runs of agent-for-QEC", "",
         "Only runs with status **valid** may be used in the paper. Read a project's RUN_INFO.md before its results.",
         "", "| project | version | commit | initialised | rounds | facts | status | note |", "|---|---|---|---|---|---|---|---|"]
    for ts, name, kv, nf in sorted(rows):
        note = str(kv.get("run_note", "")).replace("|", "\\|").replace("\n", " ")
        L.append(f"| [{name}]({name}/RUN_INFO.md) | v{kv.get('system_version', '<2.2')} | "
                 f"`{str(kv.get('repo_head', '-'))[:8]}` | {_t(ts)} | {kv.get('round', '-')} | {nf} | "
                 f"{kv.get('run_status', 'unrecorded')} | {note} |")
    out = root / "RUNS.md"
    out.write_text("\n".join(L) + "\n")
    return out
