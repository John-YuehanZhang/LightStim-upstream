#!/usr/bin/env python3
"""Round loop for agent-for-QEC v2.

  orchestrate.py --project P --rounds 3 --workers w1,w2

One round:
  1. main agent (one process): reads the store, writes route registry, guidance
     and one assignment per worker, or declares the task done
  2. workers (one process each, in parallel, each on its own credential lease)
  3. reviewer (one process) on every newly accepted fact whose review is pending
  4. novelty auditor (one process) on every fact with review ok and novelty pending
  5. ledger re-rendered
Accounts are switched only between processes, so no running agent is ever
interrupted by a credential change.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "lib"))
sys.path.insert(0, str(V2 / "runner"))
from store import Store  # noqa: E402
from launch import run  # noqa: E402
from providers import NoCredential  # noqa: E402
from render import render_ledger  # noqa: E402


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def launch(project, role, worker, config):
    try:
        r = run(project, role, worker, config_path=config)
    except NoCredential as e:
        r = {"exit": 3, "error": str(e)}
    log(f"{role}/{worker}: " + json.dumps({k: r.get(k) for k in ("exit", "account", "turns", "cost_usd", "error")}))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--workers", default="w1,w2")
    ap.add_argument("--config")
    ap.add_argument("--skip-main-first", action="store_true",
                    help="use existing open assignments for the first round")
    a = ap.parse_args()
    workers = [w for w in a.workers.split(",") if w]
    st = Store(a.project)
    for i in range(a.rounds):
        rnd = st.current_round() + 1
        st.set("round", rnd)
        log(f"=== round {rnd} ===")
        if not (i == 0 and a.skip_main_first):
            r = launch(a.project, "main", "main", a.config)
            if r.get("exit") == 3:
                log("no credential for main agent; stopping")
                break
        st = Store(a.project)
        if st.get("status") == "done":
            log("main agent declared the task done")
            break
        todo = [w for w in workers if st.assignment(w) is not None]
        if not todo:
            log("no open assignments; stopping")
            break
        with ThreadPoolExecutor(max_workers=len(todo)) as ex:
            list(ex.map(lambda w: launch(a.project, "worker", w, a.config), todo))
        for w in todo:
            st.con.execute("UPDATE assignments SET status='done' WHERE worker=? AND status='open'", (w,))
        st.con.commit()
        if [f for f in st.facts() if f["review_status"] == "pending"]:
            launch(a.project, "reviewer", "reviewer", a.config)
        st = Store(a.project)
        if [f for f in st.facts() if f["review_status"] == "ok" and f["novelty_status"] == "pending"]:
            launch(a.project, "novelty", "novelty", a.config)
        log("ledger: " + str(render_ledger(Store(a.project))))


if __name__ == "__main__":
    main()
