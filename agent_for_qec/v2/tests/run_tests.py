#!/usr/bin/env python3
"""Regression tests for the v2 gate, store and service. Run from the repository root:
    PYTHONPATH=. /home/yuehan/miniconda3/envs/light_stim/bin/python agent_for_qec/v2/tests/run_tests.py
Uses throw-away QEC_RUNTIME_ROOT / QEC_RESULTS_ROOT directories."""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
REPO = V2.parents[1]
TMP = tempfile.mkdtemp(prefix="qec_tests_", dir=os.environ.get("QEC_TEST_TMP"))
os.environ["QEC_RUNTIME_ROOT"] = TMP + "/runtime"
os.environ["QEC_RESULTS_ROOT"] = TMP + "/results"
sys.path.insert(0, str(V2)); sys.path.insert(0, str(V2 / "lib"))
import qec  # noqa: E402
from store import Store  # noqa: E402

fails = []
def check(name, cond, info=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {info}")
    if not cond:
        fails.append(name)

def run(argv, role="human", worker="human", project="t", **kw):
    return qec.dispatch(argv, project, role, worker, cwd=str(REPO), **kw)

Path(TMP, "task.md").write_text("# test task\n")
run(["init", "--task", f"{TMP}/task.md"])

def submit(d):
    out, code = run(["submit", str(d)], role="worker", worker="tester")
    try:
        head = json.loads(out[: out.index("}") + 1])
    except Exception:
        head = {"outcome": "unparsable", "raw": out[:300]}
    return head, out

# ---- fixtures
for name, want in [("good_mem_d3", "accepted"), ("good_cnot_d3", "accepted"),
                   ("bad_sign_d3", "rejected"), ("bad_swapped_d3", "rejected")]:
    h, out = submit(V2 / "tests/fixtures" / name)
    check(f"fixture {name} -> {want}", h.get("outcome") == want, f"got {h.get('outcome')}: {str(h.get('reason'))[:120]}")

# ---- exploits
exp = json.loads((V2 / "tests/exploits/expected.json").read_text())
for name, want in exp.items():
    h, out = submit(V2 / "tests/exploits" / name)
    check(f"exploit {name} -> {want}", h.get("outcome") == want, f"got {h.get('outcome')}: {str(h.get('reason'))[:160]}")

st = Store("t")
good = st.facts()[0]["id"] if st.facts() else None
# ---- store: duplicate / revoked resubmission
h, _ = submit(V2 / "tests/fixtures/good_mem_d3")
check("resubmitting an accepted bundle -> duplicate", h.get("outcome") == "duplicate", str(h.get("outcome")))
fid = next(f["id"] for f in st.facts() if "memory" in f["kind"])
run(["revoke", fid[:12], "--reason", "test"])
h, _ = submit(V2 / "tests/fixtures/good_mem_d3")
r = Store("t").fact(fid, exact=True)
check("resubmitting a revoked bundle does not revive it", r["status"] == "revoked" and h.get("outcome") == "duplicate",
      f"status={r['status']} outcome={h.get('outcome')}")
# ---- dependency references
d = Path(TMP) / "dep_sub"; import shutil
shutil.copytree(V2 / "tests/fixtures/good_mem_d3", d)
cnot = next(f["id"] for f in Store("t").facts() if f["kind"] == "logical_gate")
spec = json.loads((d / "submission.json").read_text()); spec["title"] += " dep"; spec["depends_on"] = [cnot[:6]]
(d / "submission.json").write_text(json.dumps(spec))
h, _ = submit(d)
check("6-character dependency prefix is refused", h.get("outcome") == "error", str(h.get("reason"))[:100])
spec["depends_on"] = [cnot[:10]]; (d / "submission.json").write_text(json.dumps(spec))
h, _ = submit(d)
f = Store("t").fact(h.get("fact_id") or "x", exact=True)
check("10-character dependency prefix is stored as the full id",
      f is not None and json.loads(f["depends_on"]) == [cnot], str(h.get("outcome")))
run(["revoke", cnot[:12], "--reason", "cascade test"])
f = Store("t").fact(h.get("fact_id") or "x", exact=True)
check("revoking a dependency cascades", f is not None and f["status"] == "revoked", f["status"] if f else "missing")
# ---- roles and memory kinds
out, code = run(["assign", "w1", "--text", "x"], role="worker", worker="w1")
check("worker cannot assign", code == 13, out.strip()[:80])
out, code = run(["submit", str(V2 / "tests/fixtures/good_mem_d3")], role="main", worker="main")
check("main cannot submit", code == 13, out.strip()[:80])
out, code = run(["memory", "add", "--kind", "guidance", "--claim", "x"], role="worker", worker="w1")
check("worker cannot write guidance via memory add", code == 13, out.strip()[:80])
out, code = run(["memory", "add", "--kind", "verification", "--claim", "x"], role="worker", worker="w1")
check("nobody writes verification memory by hand", code == 13, out.strip()[:80])
# ---- path confinement (service mode)
out, code = run(["memory", "add", "--kind", "finding", "--claim", "x", "--evidence-file",
                 "/nvme2n1/yuehan_zhang/.secrets/claude_oauth_tokens.txt"], role="worker", worker="w1",
                allowed_roots=[TMP + "/wdir"])
check("service refuses files outside the worker directory", code == 13, out.strip()[:80])
# ---- service over a socket
from service import Service
svc = Service("t", Path(os.environ["QEC_RUNTIME_ROOT"]))
wdir = Path(TMP, "wdir"); wdir.mkdir(exist_ok=True)
sd = svc.endpoint("worker", "w9", [str(wdir)])
env = {**os.environ, "QEC_SOCKET": sd + "/sock"}
p = subprocess.run([sys.executable, str(V2 / "qec.py"), "status"], env=env, capture_output=True, text=True, cwd=str(wdir))
check("socket client gets status with the bound role", "role=worker worker=w9" in p.stdout, p.stdout[:120].replace("\n", " "))
p = subprocess.run([sys.executable, str(V2 / "qec.py"), "assign", "w1", "--text", "x"], env={**env, "QEC_ROLE": "main"},
                   capture_output=True, text=True, cwd=str(wdir))
check("QEC_ROLE in the environment cannot raise the role", p.returncode == 13, p.stdout.strip()[:80])
shutil.copytree(V2 / "tests/fixtures/bad_swapped_d3", wdir / "sub")
p = subprocess.run([sys.executable, str(V2 / "qec.py"), "submit", "sub"], env=env, capture_output=True, text=True, cwd=str(wdir))
check("submit over the socket runs the gate", '"outcome": "duplicate"' in p.stdout or '"outcome": "rejected"' in p.stdout,
      p.stdout[:120].replace("\n", " "))
svc.shutdown()
# ---- launcher: prompts and harness settings (no model call)
sys.path.insert(0, str(V2 / "runner"))
import launch  # noqa: E402
st = Store("t"); st.set("round", 1)
for role in ("main", "worker", "refuter", "novelty"):
    pr = launch.build_prompt(role, "t", "w1" if role == "worker" else role, st)
    left = [t for t in ("{QEC}", "{PY}", "{REPO}", "{WORKDIR}", "{RESULTS}", "{TASK}", "{PROJECT}") if t in pr]
    check(f"{role} prompt has no unreplaced placeholders", not left, str(left))
    import re as _re
    bad = [w for w in ("budget", "quota", "headroom", "usage window", "acct") if w in pr.lower()]
    bad += _re.findall(r"at most \d+ (?:subagent|worker|process)", pr.lower())
    check(f"{role} prompt carries no operator/resource policy", not bad, str(bad))
    web = {"WebSearch", "WebFetch"} & set(launch.ROLE_TOOLS[role])
    check(f"{role} web tools", bool(web) == (role == "novelty"), str(sorted(web)))
d = launch.run("t", "worker", "w1", dry_run=True)
check("dry run: harness flags", all(x in d["cmd"] for x in ("--tools", "--strict-mcp-config", "dontAsk")), "")
check("dry run: prompt archived", Path(d["prompt_file"]).exists(), d["prompt_file"])
# ---- agent sandbox (no model call): what an agent process can see and do
svc = Service("t", Path(os.environ["QEC_RUNTIME_ROOT"]))
wdir = Store("t").dir / "workers" / "w1"; wdir.mkdir(parents=True, exist_ok=True)
home = Path(TMP, "home"); home.mkdir(exist_ok=True)
sd = svc.endpoint("worker", "w1", [str(wdir)])
probe = f"""
{{ [ -d {REPO}/.git ] && [ -z "$(ls -A {REPO}/.git)" ]; }} || {{ [ -e {REPO}/.git ] && [ ! -s {REPO}/.git ]; }} && echo GIT_HIDDEN
test -z "$(ls -A {REPO}/agent_for_qec/phase1)" && echo V1_HIDDEN
test -e /nvme2n1/yuehan_zhang/.secrets/claude_oauth_tokens.txt || echo SECRETS_HIDDEN
test -e {launch.RUNTIME_ROOT}/t/store.sqlite || echo STORE_HIDDEN
touch {REPO}/zz_should_fail 2>/dev/null || echo REPO_RO
touch {wdir}/ok && echo WDIR_RW
{launch.PY} -c 'import lightstim, stim; print("IMPORT_OK")'
{launch.QEC_CMD} status | grep -q 'role=worker worker=w1' && echo SOCKET_OK
"""
cmd = launch.sandbox_cmd(["bash", "-c", probe], project="t", wdir=wdir, home=home, sock_dir=sd)
p = subprocess.run(cmd, env=launch.sandbox_env({"CLAUDE_CODE_OAUTH_TOKEN": "tok-SENTINEL"}, home),
                   capture_output=True, text=True, timeout=120)
for tag in ("GIT_HIDDEN", "V1_HIDDEN", "SECRETS_HIDDEN", "STORE_HIDDEN", "REPO_RO", "WDIR_RW", "IMPORT_OK", "SOCKET_OK"):
    check(f"agent sandbox: {tag}", tag in p.stdout, p.stderr[-200:])
check("agent sandbox: credential not on the command line", not any("SENTINEL" in c for c in cmd), "")
svc.shutdown()
# ---- quota bookkeeping
import quota  # noqa: E402
check("headroom: account answered without window data -> usable", quota.headroom({"ok": True}) == 0.5, "")
check("headroom: missing utilization tolerated", quota.headroom({"ok": True, "five_hour": {}, "seven_day": {"utilization": 0.3}}) == 0.7, "")
rt = Path(os.environ["QEC_RUNTIME_ROOT"])
quota.block_model(rt, "acctX", "opus", time.time() + 60, "test")
snap = {"accounts": {"acctX": {"headroom": 0.9, "models": {}}}}
check("per-model block until reset", not quota.model_ok(snap, "acctX", "opus", rt) and quota.model_ok(snap, "acctX", "haiku", rt), "")
from render import _cell  # noqa: E402
check("ledger cells escape pipes and newlines", _cell("a|b\nc") == "a\\|b c", _cell("a|b\nc"))
# ---- v2.2: operating rules
st = Store("t")
st.set_operating_rules("all", "- Assign as many workers as the work needs.")
st.set_operating_rules("w1", "- You may run at most 2 subagent(s) at a time.")
st.set_operating_rules("w2", "- You may run at most 0 subagent(s) at a time.")
out, code = run(["status"], role="worker", worker="w1")
check("status shows round rules and this process's rules only",
      "as many workers as the work needs" in out and "at most 2 subagent" in out and "at most 0" not in out, "")
out, code = run(["memory", "add", "--kind", "operating_rules", "--claim", "x"], role="main", worker="main")
check("agents cannot write operating rules", code == 13, out.strip()[:80])
# ---- v2.2: challenges, refutations, human adjudication
fact = Store("t").facts()[0]
out, code = run(["challenge", fact["id"][:12], "--n", "2", "--focus", "x"], role="worker", worker="w1")
check("worker cannot open a challenge", code == 13, out.strip()[:80])
out, code = run(["challenge", fact["id"][:12], "--n", "2", "--focus", "does the flow describe CNOT?"],
                role="main", worker="main")
check("main opens a challenge", code == 0 and "challenge #1" in out, out.strip()[:100])
check("challenged fact state", Store("t").fact(fact["id"], exact=True)["refute_status"] == "challenged", "")
st = Store("t"); st.assign("rf1_1", "challenge #1: fact " + fact["id"])
out, code = run(["memory", "search", "--limit", "3"], role="refuter", worker="rf1_1")
check("refuter cannot read the submitters' shared memory", code == 13, out.strip()[:80])
out, code = run(["refute", "1", "--verdict", "refuted", "--text", "wrong"], role="refuter", worker="rf1_1")
check("refutation without reproducible evidence is refused", code != 0, out.strip()[:100])
out, code = run(["refute", "1", "--verdict", "refuted", "--text", "x"], role="refuter", worker="rf9_9")
check("refuter cannot answer a challenge not assigned to it", code == 13, out.strip()[:80])
ev = wdir_ev = Path(TMP, "ev.py"); ev.write_text("print('defect')\n# output: defect\n")
out, code = run(["refute", "1", "--verdict", "refuted", "--text", "flows wrong", "--evidence-file", str(ev)],
                role="refuter", worker="rf1_1")
check("refutation with evidence recorded", code == 0, out.strip()[:100])
r = Store("t").fact(fact["id"], exact=True)
check("refuted fact stays active, pending human", r["status"] == "active" and r["refute_status"] == "refuted_pending_human",
      f"{r['status']} {r['refute_status']}")
run(["render"])
led = (Path(os.environ["QEC_RESULTS_ROOT"]) / "t" / "LEDGER.md").read_text()
check("ledger lists passed-but-refuted facts in their own section",
      "Passed the gate but refuted" in led and fact["id"][:12] in led.split("Passed the gate but refuted")[1].split("## ")[0],
      "")
out, code = run(["adjudicate", fact["id"][:12], "--reject", "--reason", "refuter misread the flow"])
check("human rejects the refutation", code == 0 and Store("t").fact(fact["id"], exact=True)["refute_status"]
      == "refutation_rejected", out.strip()[:80])
# ---- v2.2: run archive
from archive import write_run_info, write_runs_index  # noqa: E402
Path(TMP, "task2.md").write_text("# archived task\n")
out, code = run(["init", "--task", f"{TMP}/task2.md"], project="arch")
ri = Store("arch").dir / "RUN_INFO.md"
check("init writes RUN_INFO.md with version and commit", ri.exists() and "system version: v2.2" in ri.read_text(), out[:120])
out, code = run(["runinfo", "--status", "void", "--note", "test run"], project="arch")
idx = (Path(os.environ["QEC_RUNTIME_ROOT"]) / "RUNS.md").read_text()
check("RUNS.md index lists the project with its status", "arch" in idx and "void" in idx, idx[-200:])
# ---- v2.2: network audit
from audit_network import audit  # noqa: E402
lg = Store("t").dir / "logs"; lg.mkdir(exist_ok=True)
(lg / "20260101_000000_r1_worker_w1_acctX.net.log").write_text(
    json.dumps({"ts": 1, "process": "worker/w1", "method": "CONNECT", "host": "api.anthropic.com", "port": 443}) + "\n" +
    json.dumps({"ts": 2, "process": "worker/w1", "method": "CONNECT", "host": "arxiv.org", "port": 443}) + "\n")
(lg / "20260101_000000_r1_worker_w1_acctX.jsonl").write_text(json.dumps(
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
     "input": {"command": "python -c 'import urllib.request'"}}]}}) + "\n")
rep, flagged = audit("t")
txt = rep.read_text()
check("network audit flags a non-harness host and network code", flagged and "arxiv.org" in txt and "urllib" in txt
      and "api.anthropic.com:443" not in txt, "")
# ---- v2.2: orchestrator turns challenges into refuter assignments and writes round rules
import argparse as _ap  # noqa: E402
import orchestrate  # noqa: E402
o = orchestrate.Orchestrator(_ap.Namespace(project="t", config=None, rounds=1, wait_hours=2, max_wait_hours=0,
                                           skip_main_first=False))
st = Store("t")
f2 = st.facts()[-1]
cid = st.add_challenge(f2["id"], 2, "check the layout claim")
names = o.open_refuters(st)
check("each challenge becomes n refuter assignments", [n for n in names if n.startswith(f"rf{cid}_")] ==
      [f"rf{cid}_1", f"rf{cid}_2"] and f"challenge #{cid}" in Store("t").assignment(f"rf{cid}_1")["text"], str(names))
check("refuter assignments are not duplicated", o.open_refuters(Store("t")) == names, "")
o.round_rules(st, [])
rules = "\n".join(r["claim"] for r in Store("t").operating_rules_for("w1"))
check("round rules: unlimited workers and the no-internet rule", "no limit on their number" in rules and "internet" in rules, rules[:120])
out, code = run(["assign", "w42", "--text", "x"], role="main", worker="main")
check("main may assign any number of workers (w42 accepted)", code == 0, out.strip()[:80])
out, code = run(["assign", "bob", "--text", "x"], role="main", worker="main")
check("worker names must be w<n>", code != 0, out.strip()[:80])
check("open_workers lists assigned workers", "w42" in o.open_workers(Store("t")), "")
rs = [c.get("resources") for fx in Store("t").facts(None) for c in json.loads(fx["verdict"])["checks"]
      if c.get("check") == "P2-P4_circuit"]
check("facts record raw resource quantities", rs and all(r and "full_circuit" in r and
      r["full_circuit"]["spacetime_volume_qubit_moments"] > 0 for r in rs), str(rs)[:200])
o.round_rules(st, [], closing=True)
check("closing-pass rules say no assignments run", "closing pass" in Store("t").operating_rules_for("x")[0]["claim"], "")
o.service.shutdown()
# ---- render
out, code = run(["render"])
check("render works", code == 0, out.strip()[:80])
print(f"\n{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
print("tmp:", TMP)
sys.exit(1 if fails else 0)
