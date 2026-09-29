#!/usr/bin/env python3
"""Round loop for agent-for-QEC v2, quota-aware.

  orchestrate.py --project P --rounds 3 [--wait-hours 2] [--max-wait-hours 72]

One round:
  0. the number of workers is fixed: min([orchestrator].max_workers, accounts
     with quota for the worker model); names w1..wN are stored as
     `workers_this_round` (the main agent sees only the names)
  1. main agent: reads the store, writes route registry, guidance and one
     assignment per worker, or declares the task done
  2. workers, in waves of as many processes as there are free accounts with
     quota (parallelism changes speed, not results). A worker cut off by a
     usage limit keeps its assignment open and is relaunched on another
     account; one that fails otherwise is relaunched up to max_relaunch times
  3. reviewer on newly accepted facts, 4. novelty auditor, 5. ledger rendered.
If no account has quota for a role, the loop sleeps --wait-hours, re-reads the
quota and resumes. Operator limits live in config/models.toml, never in prompts.
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
from providers import NoCredential, load_config, free_accounts, unleased, invalidate_quota  # noqa: E402
from render import render_ledger  # noqa: E402
from service import Service  # noqa: E402


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


class Orchestrator:
    def __init__(self, a):
        self.a = a
        self.cfg = load_config(a.config)
        self.ocfg = self.cfg.get("orchestrator", {})
        self.waited = 0.0
        self.service = Service(a.project, RUNTIME_ROOT)

    def capacity(self, role: str, force: bool = False) -> int:
        """Accounts that have quota for the role's model and are not held by another process."""
        rcfg = self.cfg["roles"][role]
        if rcfg["provider"] != "claude_oauth":
            return 1
        acc = free_accounts(self.cfg["providers"]["claude_oauth"], rcfg["model"], RUNTIME_ROOT, force=force)
        return len(unleased(RUNTIME_ROOT, acc))

    def wait_for(self, role: str, need: int = 1) -> int:
        """Block until at least `need` accounts are usable for `role`; return the capacity."""
        cap = self.capacity(role)
        while cap < need:
            if self.waited >= self.a.max_wait_hours:
                log(f"waited {self.waited:.1f} h in total without quota; giving up")
                self.service.shutdown()
                sys.exit(3)
            log(f"no quota for role {role} (capacity {cap} < {need}); sleeping {self.a.wait_hours} h")
            time.sleep(self.a.wait_hours * 3600)
            self.waited += self.a.wait_hours
            invalidate_quota(RUNTIME_ROOT)
            cap = self.capacity(role, force=True)
        return cap

    def launch(self, role, worker):
        """Run one process; waits for quota and retries while it is cut off by a usage limit."""
        while True:
            self.wait_for(role)
            try:
                r = run(self.a.project, role, worker, config_path=self.a.config, service=self.service)
            except NoCredential as e:  # capacity taken by another process in the meantime
                log(f"{role}/{worker}: {e}; retrying after quota refresh")
                invalidate_quota(RUNTIME_ROOT)
                time.sleep(60)
                continue
            log(f"{role}/{worker}: " + json.dumps({k: r.get(k) for k in ("exit", "account", "turns", "cost_usd",
                                                                         "hit_limit", "limit_type", "is_error")}))
            if r.get("hit_limit"):
                log(f"{role}/{worker} was cut off by a usage limit on {r['account']}; relaunching elsewhere")
                continue
            return r

    def workers_phase(self, st: Store, workers):
        fails = {w: 0 for w in workers}
        max_relaunch = int(self.ocfg.get("max_relaunch", 2))
        while True:
            st = Store(self.a.project)
            todo = [w for w in workers if st.assignment(w) is not None and fails[w] <= max_relaunch]
            if not todo:
                break
            cap = self.wait_for("worker")
            wave = todo[:cap]
            log(f"worker wave: {wave} (capacity {cap}, pending {todo})")
            with ThreadPoolExecutor(max_workers=len(wave)) as ex:
                results = dict(zip(wave, ex.map(lambda w: self.launch("worker", w), wave)))
            st = Store(self.a.project)
            for w, r in results.items():
                if r.get("is_error") or r.get("exit") not in (0, None):
                    fails[w] += 1
                    log(f"{w} failed (exit {r.get('exit')}); attempt {fails[w]} of {max_relaunch + 1}")
                    continue
                st.close_assignments("done", w)
        n = Store(self.a.project).close_assignments("abandoned")
        if n:
            log(f"{n} assignment(s) abandoned after repeated failures")

    def main_phase(self) -> bool:
        for i in range(int(self.ocfg.get("main_attempts", 3))):
            r = self.launch("main", "main")
            if not r.get("is_error") and r.get("exit") == 0:
                return True
            log(f"main agent failed (exit {r.get('exit')}), attempt {i + 1}")
        return False

    def plan_workers(self, st: Store) -> list:
        cap = self.wait_for("worker")
        n = max(1, min(int(self.ocfg.get("max_workers", 6)), cap))
        ws = [f"w{i + 1}" for i in range(n)]
        st.set("workers_this_round", ws)
        log(f"workers this round: {ws} (accounts usable for workers: {cap})")
        return ws

    def loop(self):
        a = self.a
        st = Store(a.project)
        try:
            for i in range(a.rounds):
                if i == 0 and a.skip_main_first:
                    workers = st.get("workers_this_round") or []
                else:
                    rnd = st.current_round() + 1
                    st.set("round", rnd)
                    log(f"=== round {rnd} ===")
                    workers = self.plan_workers(st)
                    if not self.main_phase():
                        log("main agent failed repeatedly; stopping")
                        break
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
                st = Store(a.project)
        finally:
            self.service.shutdown()
        log("orchestrator finished")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--config")
    ap.add_argument("--wait-hours", type=float, default=2.0)
    ap.add_argument("--max-wait-hours", type=float, default=72.0)
    ap.add_argument("--skip-main-first", action="store_true",
                    help="first round: reuse the open assignments instead of running the main agent")
    Orchestrator(ap.parse_args()).loop()


if __name__ == "__main__":
    main()
