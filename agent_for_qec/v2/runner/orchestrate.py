#!/usr/bin/env python3
"""Round loop for agent-for-QEC v2, quota-aware.

  orchestrate.py --project P --rounds 3 [--wait-hours 2] [--max-wait-hours 72]

One round:
  0. the run conditions (no limit on the number of workers; compute per
     process; no internet while solving) are written as the round's
     `operating_rules` memory (agents read it through `qec.py status`)
  1. main agent: reads the store, writes route registry, guidance and one
     assignment per worker, opens challenges on facts (how many refuters, what
     to attack), or declares the task done
  2. workers and refuters, as many as the main agent assigned, in waves of as
     many processes as there are free accounts with quota (parallelism changes
     speed, not results; the number of assignments is never capped). A process
     cut off by a usage limit keeps its assignment open and is relaunched on
     another account; one that fails otherwise is relaunched up to max_relaunch
  3. ledger rendered.
After the last round, a closing pass lets the main agent challenge facts that
were never challenged; then refuters, the novelty auditor (on facts that are not
refuted), the run archive (RUN_INFO.md, RUNS.md) and the network audit run.
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
from archive import write_run_info, write_runs_index  # noqa: E402
from audit_network import audit  # noqa: E402


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

    def open_refuters(self, st: Store) -> list:
        """Turn the main agent's open challenges into refuter assignments."""
        names = []
        for ch in st.open_challenges():
            for i in range(1, ch["n"] + 1):
                name = f"rf{ch['id']}_{i}"
                if st.assignment(name) is None and not st.con.execute(
                        "SELECT 1 FROM assignments WHERE worker=?", (name,)).fetchone():
                    st.assign(name, f"challenge #{ch['id']}: fact {ch['fact_id']}\n\n"
                                    f"What the main agent asks you to attack:\n{ch['focus']}")
                names.append(name)
        return names

    def process_phase(self, items):
        """items: list of (role, name) with an open assignment each; run them in waves."""
        fails = {n: 0 for _, n in items}
        max_relaunch = int(self.ocfg.get("max_relaunch", 2))
        while True:
            st = Store(self.a.project)
            todo = [(r, n) for r, n in items if st.assignment(n) is not None and fails[n] <= max_relaunch]
            if not todo:
                break
            cap = self.wait_for("worker")
            wave = todo[:cap]
            log(f"wave: {[n for _, n in wave]} (capacity {cap}, pending {[n for _, n in todo]})")
            with ThreadPoolExecutor(max_workers=len(wave)) as ex:
                results = dict(zip([n for _, n in wave], ex.map(lambda rn: self.launch(*rn), wave)))
            st = Store(self.a.project)
            for n, r in results.items():
                if r.get("is_error") or r.get("exit") not in (0, None):
                    fails[n] += 1
                    log(f"{n} failed (exit {r.get('exit')}); attempt {fails[n]} of {max_relaunch + 1}")
                    continue
                st.close_assignments("done", n)
        st = Store(self.a.project)
        n = st.close_assignments("abandoned")
        if n:
            log(f"{n} assignment(s) abandoned after repeated failures")
        for ch in st.open_challenges():
            st.close_challenge(ch["id"])

    def round_rules(self, st: Store, workers, closing: bool = False) -> None:
        per = int(self.ocfg.get("cpus_per_agent", 16))
        lines = []
        if closing:
            lines.append("- This is the closing pass of the run: no worker assignments will be executed. Only open "
                         "challenges on facts that should be attacked; do not write assignments.")
        else:
            lines.append("- Assign as many workers as the work needs (names w1, w2, ...); there is no limit on their "
                         "number. Each gets exactly one assignment. They run in parallel as far as accounts allow, "
                         "otherwise in waves.")
        lines += [f"- Each process has {per} CPU cores; do not run more than {per} parallel jobs.",
                  "- A single tool call is limited to about 10 minutes. Run longer jobs in the background, have them "
                  "write a .done file, and wait for it before ending your turn.",
                  "- Do not access the internet while solving (no web pages, no paper downloads, no package "
                  "installation), by any means including scripts. Network access is logged. (The novelty auditor "
                  "is exempt.)"]
        st.set_operating_rules("all", "\n".join(lines))

    def main_phase(self) -> bool:
        for i in range(int(self.ocfg.get("main_attempts", 3))):
            r = self.launch("main", "main")
            if not r.get("is_error") and r.get("exit") == 0:
                return True
            log(f"main agent failed (exit {r.get('exit')}), attempt {i + 1}")
        return False

    def open_workers(self, st: Store) -> list:
        return [x["worker"] for x in st.open_assignments() if not x["worker"].startswith("rf")]

    def closing(self):
        st = Store(self.a.project)
        pending = self.open_refuters(st)           # challenges opened in a round that ended early
        if pending:
            self.process_phase([("refuter", n) for n in pending])
        st = Store(self.a.project)
        if [f for f in st.facts() if f["refute_status"] == "unchallenged"]:
            st.set("round", st.current_round() + 1)
            self.round_rules(st, [], closing=True)
            log(f"=== closing pass (round {st.current_round()}) ===")
            self.main_phase()
            st = Store(self.a.project)
            st.close_assignments("abandoned")          # worker assignments are not run in the closing pass
            self.process_phase([("refuter", n) for n in self.open_refuters(st)])
        st = Store(self.a.project)
        if [f for f in st.facts() if f["novelty_status"] == "pending"
                and f["refute_status"] not in ("refuted_pending_human", "challenged")]:
            self.launch("novelty", "novelty")
        st = Store(self.a.project)
        log("ledger: " + str(render_ledger(st)))
        log(f"run info: {write_run_info(st)}; index: {write_runs_index()}")
        out, flagged = audit(self.a.project)
        log(f"network audit: {out} ({'FINDINGS: review by hand' if flagged else 'clean'})")

    def loop(self):
        a = self.a
        st = Store(a.project)
        try:
            for i in range(a.rounds):
                if Store(a.project).get("stop_requested"):
                    log("stop requested by the portfolio planner; no further rounds")
                    break
                if not (i == 0 and a.skip_main_first):
                    rnd = st.current_round() + 1
                    st.set("round", rnd)
                    log(f"=== round {rnd} ===")
                    self.round_rules(st, [])
                    if not self.main_phase():
                        log("main agent failed repeatedly; stopping")
                        break
                st = Store(a.project)
                workers = self.open_workers(st)
                log(f"workers assigned this round: {workers}")
                if st.get("status") == "done":
                    log("main agent declared the task done")
                    break
                if not workers and not st.open_challenges():
                    log("no open assignments or challenges after the main agent; stopping")
                    break
                refuters = self.open_refuters(st)
                self.process_phase([("worker", w) for w in workers] + [("refuter", n) for n in refuters])
                log("ledger: " + str(render_ledger(Store(a.project))))
                st = Store(a.project)
            self.closing()
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
