#!/usr/bin/env python3
"""Round loop for agent-for-QEC v2, quota-aware.

  orchestrate.py --project P --rounds 3 --workers w1,w2 [--wait-hours 2] [--max-wait-hours 72]

One round:
  1. main agent (one process): reads the store, writes route registry, guidance
     and one assignment per worker, or declares the task done
  2. workers, in waves: as many in parallel as there are accounts with quota
     left (parallelism changes speed, not results). A worker cut off by a usage
     limit keeps its assignment open and is relaunched on another account.
  3. reviewer on newly accepted facts, 4. novelty auditor, 5. ledger rendered.
If no account has quota for a role, the loop sleeps --wait-hours, re-reads the
quota and resumes. Accounts are only switched between processes.
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
from store import Store, RUNTIME_ROOT  # noqa: E402
from launch import run  # noqa: E402
from providers import NoCredential, load_config, free_accounts, invalidate_quota  # noqa: E402
from render import render_ledger  # noqa: E402

MAX_RELAUNCH = 3


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


class Orchestrator:
    def __init__(self, a):
        self.a = a
        self.cfg = load_config(a.config)
        self.waited = 0.0

    def capacity(self, role: str, force: bool = False) -> int:
        rcfg = self.cfg["roles"][role]
        if rcfg["provider"] != "claude_oauth":
            return 1
        return len(free_accounts(self.cfg["providers"]["claude_oauth"], rcfg["model"], RUNTIME_ROOT, force=force))

    def wait_for(self, role: str, need: int = 1) -> int:
        """Block until at least `need` accounts have quota for `role`; return the capacity."""
        cap = self.capacity(role)
        while cap < need:
            if self.waited >= self.a.max_wait_hours:
                log(f"waited {self.waited:.1f} h in total without quota; giving up")
                sys.exit(3)
            log(f"no quota for role {role} (capacity {cap} < {need}); sleeping {self.a.wait_hours} h")
            time.sleep(self.a.wait_hours * 3600)
            self.waited += self.a.wait_hours
            invalidate_quota(RUNTIME_ROOT)
            cap = self.capacity(role, force=True)
        return cap

    def launch(self, role, worker):
        while True:
            self.wait_for(role)
            try:
                r = run(self.a.project, role, worker, config_path=self.a.config)
            except NoCredential as e:  # all capacity leased by other processes or just exhausted
                log(f"{role}/{worker}: {e}; retrying after quota refresh")
                invalidate_quota(RUNTIME_ROOT)
                time.sleep(60)
                continue
            log(f"{role}/{worker}: " + json.dumps({k: r.get(k) for k in
                                                   ("exit", "account", "headroom", "turns", "cost_usd", "hit_limit")}))
            return r

    def workers_phase(self, st: Store, workers):
        attempts = {w: 0 for w in workers}
        while True:
            todo = [w for w in workers if st.assignment(w) is not None and attempts[w] < MAX_RELAUNCH]
            if not todo:
                return
            cap = self.wait_for("worker")
            wave = todo[:cap]
            log(f"worker wave: {wave} (capacity {cap}, pending {todo})")
            with ThreadPoolExecutor(max_workers=len(wave)) as ex:
                results = dict(zip(wave, ex.map(lambda w: self.launch("worker", w), wave)))
            for w, r in results.items():
                attempts[w] += 1
                if r.get("hit_limit"):
                    log(f"{w} was cut off by a usage limit; assignment stays open for relaunch")
                    continue
                st.con.execute("UPDATE assignments SET status='done' WHERE worker=? AND status='open'", (w,))
            st.con.commit()

    def loop(self):
        a = self.a
        workers = [w for w in a.workers.split(",") if w]
        st = Store(a.project)
        for i in range(a.rounds):
            if not (i == 0 and a.skip_main_first):
                rnd = st.current_round() + 1
                st.set("round", rnd)
                log(f"=== round {rnd} ===")
                self.launch("main", "main")
            st = Store(a.project)
            if st.get("status") == "done":
                log("main agent declared the task done")
                break
            if not [w for w in workers if st.assignment(w) is not None]:
                log("no open assignments after the main agent; stopping")
                break
            self.workers_phase(st, workers)
            st = Store(a.project)
            if [f for f in st.facts() if f["review_status"] == "pending"]:
                self.launch("reviewer", "reviewer")
            st = Store(a.project)
            if [f for f in st.facts() if f["review_status"] == "ok" and f["novelty_status"] == "pending"]:
                self.launch("novelty", "novelty")
            log("ledger: " + str(render_ledger(Store(a.project))))
        log("orchestrator finished")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--workers", default="w1,w2")
    ap.add_argument("--config")
    ap.add_argument("--wait-hours", type=float, default=2.0)
    ap.add_argument("--max-wait-hours", type=float, default=72.0)
    ap.add_argument("--skip-main-first", action="store_true",
                    help="first round: reuse the open assignments instead of running the main agent")
    Orchestrator(ap.parse_args()).loop()


if __name__ == "__main__":
    main()
