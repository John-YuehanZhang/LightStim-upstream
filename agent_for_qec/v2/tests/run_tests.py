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
# ---- render
out, code = run(["render"])
check("render works", code == 0, out.strip()[:80])
print(f"\n{len(fails)} failure(s)" + (": " + ", ".join(fails) if fails else ""))
print("tmp:", TMP)
sys.exit(1 if fails else 0)
