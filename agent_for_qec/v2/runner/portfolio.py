#!/usr/bin/env python3
"""Portfolio runner: a planner agent proposes topics; every topic becomes its own
project (main agent + workers + refuters) run by orchestrate.py in a separate
process; the shared library is rebuilt from all project stores between cycles.

  portfolio.py --portfolio NAME --task tasks/explore_v1/TASK.md [--cycles N] [--origin agent]

One cycle:
  1. sync the library from every project store; propagate revocations of
     external dependencies; write the runs index
  2. planner process (reads library + topics; proposes/closes topics)
  3. every topic with status `proposed` becomes project <portfolio>__<topic>
     (initialised with the topic's TASK text) and an orchestrator is started
     for it; closed topics get their orchestrator stopped after its round
  4. wait until at least one running project finishes (or all are done), then
     start the next cycle
Nothing here caps the number of topics, workers, tokens or dollars; only
account quota and CPU slots bound parallelism (handled inside each orchestrator).
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "lib"))
sys.path.insert(0, str(V2 / "runner"))
from store import Store, RUNTIME_ROOT  # noqa: E402
from library import Library  # noqa: E402
from archive import record_init, write_run_info, write_runs_index  # noqa: E402
from launch import run  # noqa: E402
from service import Service  # noqa: E402
from providers import NoCredential, invalidate_quota  # noqa: E402

PY = sys.executable


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


class Portfolio:
    def __init__(self, a):
        self.a = a
        self.name = a.portfolio
        self.st = Store(self.name)
        self.lib = Library()
        self.procs: dict = {}          # topic name -> Popen of its orchestrator
        self.service = Service(self.name, RUNTIME_ROOT)

    # ------------------------------------------------------------ setup
    def init(self):
        if self.st.get("task_path") is None:
            self.st.set("task_path", str(Path(self.a.task).resolve()))
            self.st.set("status", "open")
            self.st.set("round", 0)
            self.st.set("origin", self.a.origin)
            self.st.set("noise_p", self.a.noise_p)
            self.st.set("library", False)           # a portfolio store holds no facts
            self.st.set("is_portfolio", True)
            record_init(self.st)
            write_run_info(self.st)
        self.rules()

    def rules(self):
        self.st.set_operating_rules("all", "\n".join([
            "- There is no limit on the number of topics you may open or on the workers, rounds, tokens or cost a "
            "topic may use; open what is worth pursuing. Topics run in parallel as far as accounts allow.",
            "- Do not access the internet (no web pages, no paper downloads), by any means including scripts. "
            "Network access is logged.",
            "- A single tool call is limited to about 10 minutes."]))

    # ------------------------------------------------------------ planner
    def planner(self) -> bool:
        self.st.set("round", self.st.current_round() + 1)
        log(f"=== planning cycle {self.st.current_round()} ===")
        for attempt in range(3):
            try:
                r = run(self.name, "planner", "planner", config_path=self.a.config, service=self.service)
            except NoCredential as e:
                log(f"planner: {e}; waiting {self.a.wait_hours} h")
                time.sleep(self.a.wait_hours * 3600)
                invalidate_quota(RUNTIME_ROOT)
                continue
            log("planner: " + json.dumps({k: r.get(k) for k in ("exit", "account", "turns", "cost_usd", "hit_limit",
                                                                 "is_error")}))
            if r.get("hit_limit"):
                continue
            return not r.get("is_error") and r.get("exit") == 0
        return False

    # ------------------------------------------------------------ topics -> projects
    def project_name(self, topic: str) -> str:
        return f"{self.name}__{topic}"

    def start_topics(self):
        for t in self.lib.topics(self.name):
            if t["status"] != "proposed":
                continue
            proj = self.project_name(t["name"])
            st = Store(proj)
            tdir = st.dir / "task"
            tdir.mkdir(exist_ok=True)
            task = tdir / "TASK.md"
            task.write_text(f"# Topic {t['name']} (layer {t['layer']}) — {t['title']}\n\n{t['task']}\n\n"
                            f"## Overall task of the portfolio (context)\n\n{Path(self.st.get('task_path')).read_text()}")
            st.set("task_path", str(task)); st.set("status", "open"); st.set("round", 0)
            st.set("origin", self.a.origin); st.set("noise_p", self.a.noise_p)
            st.set("portfolio", self.name); st.set("topic", t["name"]); st.set("layer", t["layer"])
            record_init(st)
            write_run_info(st)
            logf = RUNTIME_ROOT / f"{proj}.orchestrate.log"
            cmd = [PY, str(V2 / "runner" / "orchestrate.py"), "--project", proj, "--rounds", str(t["rounds"]),
                   "--wait-hours", str(self.a.wait_hours), "--max-wait-hours", str(self.a.max_wait_hours)]
            if self.a.config:
                cmd += ["--config", self.a.config]
            p = subprocess.Popen(cmd, stdout=open(logf, "a"), stderr=subprocess.STDOUT, start_new_session=True)
            self.procs[t["name"]] = p
            self.lib.set_topic(t["name"], status="running")
            log(f"topic {t['name']} -> project {proj} started (pid {p.pid}, {t['rounds']} rounds)")

    def stop_closed(self):
        for t in self.lib.topics(self.name):
            p = self.procs.get(t["name"])
            if t["status"] == "closed" and p is not None and p.poll() is None:
                # let the current round finish: signal the orchestrator to stop before its next round
                Store(self.project_name(t["name"])).set("stop_requested", True)
                log(f"topic {t['name']} closed by the planner; its project stops after the current round")

    def reap(self) -> list:
        done = []
        for name, p in list(self.procs.items()):
            if p.poll() is not None:
                st = Store(self.project_name(name))
                self.lib.set_topic(name, status="done" if st.get("status") == "done" else
                                   ("closed" if self.lib.topic(name)["status"] == "closed" else "stopped"),
                                   reason=st.get("done_reason") or self.lib.topic(name)["reason"] or "orchestrator exited")
                log(f"project {self.project_name(name)} finished (exit {p.returncode})")
                done.append(name)
                del self.procs[name]
        return done

    def wait_for_change(self):
        while self.procs and not any(p.poll() is not None for p in self.procs.values()):
            time.sleep(60)

    # ------------------------------------------------------------ loop
    def loop(self):
        self.init()
        try:
            for cycle in range(self.a.cycles):
                self.lib.sync()
                rev = self.lib.propagate_revocations()
                if rev:
                    log(f"revoked through external dependencies: {rev}")
                write_runs_index()
                if not self.planner():
                    log("planner failed repeatedly; stopping")
                    break
                self.stop_closed()
                self.start_topics()
                if not self.procs:
                    log("no running projects after planning; stopping")
                    break
                self.wait_for_change()
                self.reap()
            for p in self.procs.values():          # cycles exhausted: let running projects finish
                p.wait()
            self.reap()
            self.lib.sync()
            write_runs_index()
            write_run_info(self.st)
        finally:
            self.service.shutdown()
        log("portfolio finished")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--origin", default="agent", choices=["agent", "calibration"])
    ap.add_argument("--noise-p", type=float, default=1e-3)
    ap.add_argument("--config")
    ap.add_argument("--wait-hours", type=float, default=2.0)
    ap.add_argument("--max-wait-hours", type=float, default=72.0)
    Portfolio(ap.parse_args()).loop()


if __name__ == "__main__":
    main()
