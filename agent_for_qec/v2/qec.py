#!/usr/bin/env python3
"""qec.py -- the single interface to the shared state of a project.

Agents run inside a sandbox that cannot see the store; their qec.py forwards
the command over a unix socket (QEC_SOCKET) to the service in the orchestrator,
which executes it with the role bound to that socket. Run without QEC_SOCKET
(the operator), commands execute locally with role 'human'.

  status                         project overview
  task                           the task statement
  assignment [WORKER]            full text of an open assignment (default: your own)
  memory add --kind K --claim C [--evidence E | --evidence-file F] [--refs ...]
  memory search [--kind K ...] [--query Q] [--limit N] [--since-round R] [--brief]
  submit DIR [--wait SECONDS]    run the verification gate (worker); waits up to --wait seconds,
                                 then returns a submission id to query later
  submission ID                  full gate report of a submission
  facts [--all]                  list facts;   fact ID   one fact in full
  assign WORKER --file F | --text T          (main)
  guidance | registry | elaboration --file F | --text T   (main)
  done --reason R                (main, human)
  revoke ID --reason R           (main, reviewer, human)
  review ID --status ok|flagged --file F     (reviewer, human)
  novelty ID --status prior_found|no_prior_found --file F   (novelty, human)
  sign-new ID                    (human)
  render                         (human) write results/<project>/LEDGER.md
  init --task FILE [--origin agent|calibration] [--noise-p P]   (human)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sys
import textwrap
import threading
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

V2 = Path(__file__).resolve().parent
sys.path.insert(0, str(V2 / "lib"))

DOC_KINDS = {"guidance": "guidance", "registry": "route_registry", "elaboration": "elaboration"}
AGENT_MEMORY_KINDS = {"finding", "example", "counterexample", "dead_end", "obstacle", "direction", "plan", "lesson"}


class Denied(Exception):
    pass


class Ctx:
    def __init__(self, project, role, worker):
        from store import Store
        self.st = Store(project)
        self.role = role
        self.worker = worker

    def need(self, *roles):
        if self.role not in roles:
            raise Denied(f"permission denied: role '{self.role}' may not run this command (allowed: {', '.join(roles)})")


def short(s, n=160):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def read_text(args) -> str:
    if getattr(args, "file", None):
        return Path(args.file).read_text()
    if getattr(args, "text", None):
        return args.text
    raise ValueError("give --file or --text")


def fact_summary(r) -> str:
    v = json.loads(r["verdict"] or "{}")
    code = next((c for c in v.get("checks", []) if c.get("check") == "P1_code"), None)
    circ = [c for c in v.get("checks", []) if c.get("check") == "P2-P4_circuit"]
    cs = ", ".join(f"{c['name']}: dist={c.get('P4_distance', {}).get('lb')}"
                   f"{'' if c.get('P4_distance', {}).get('status') == 'exact' else '+'}"
                   f"{' FT' if c.get('P4_distance', {}).get('fault_tolerant_at_instance') else ''}" for c in circ)
    cd = f"[[{code['n']},{code['k']},{code['d']}]]" if code else "-"
    return (f"{r['id'][:12]}  {r['kind']:<19} {cd:<14} {short(r['title'], 60):<60}  {cs}  "
            f"review={r['review_status']} novelty={r['novelty_status']} origin={r['origin']}"
            + (f"  REVOKED: {short(r['revoked_reason'], 60)}" if r["status"] == "revoked" else ""))


# ------------------------------------------------------------------ commands
def c_status(cx, a):
    st = cx.st
    print(f"# project {st.project}   round {st.current_round()}   status {st.get('status', 'open')}")
    print(f"# you are role={cx.role} worker={cx.worker}")
    ws = st.get("workers_this_round")
    if ws:
        print(f"# workers available this round: {', '.join(ws)}")
    print()
    facts = st.facts()
    print(f"## verified facts ({len(facts)} active)")
    for r in facts:
        print("  " + fact_summary(r))
    rev = st.facts("revoked")
    if rev:
        print(f"## revoked facts ({len(rev)})")
        for r in rev:
            print("  " + fact_summary(r))
    print("\n## open assignments (full text: `qec.py assignment <worker>`)")
    for x in st.open_assignments():
        print(f"  {x['worker']} (round {x['round']}): {short(x['text'], 200)}")
    for kind in ("guidance", "route_registry"):
        m = st.search_memory([kind], limit=1)
        if m:
            print(f"\n## latest {kind} (round {m[0]['round']}, id {m[0]['id']})\n" + textwrap.indent(m[0]["claim"], "  "))
    n = 60 if a.full else 15
    print(f"\n## recent shared memory (last {n}; full text: `qec.py memory search`)")
    for m in st.search_memory(limit=n):
        if m["kind"] in ("guidance", "route_registry", "elaboration"):
            continue
        print(f"  #{m['id']} r{m['round']} {m['kind']:<14} {m['author']:<10} {short(m['claim'], 110)}")


def c_task(cx, a):
    p = cx.st.get("task_path")
    print(Path(p).read_text() if p else "(no task)")


def c_assignment(cx, a):
    w = a.worker or cx.worker
    x = cx.st.assignment(w)
    print(f"# assignment for {w} (round {x['round']})\n\n{x['text']}" if x else f"(no open assignment for {w})")


def c_memory(cx, a):
    st = cx.st
    if a.mem_cmd == "add":
        if a.kind not in AGENT_MEMORY_KINDS:
            raise Denied(f"memory kind '{a.kind}' cannot be added with `memory add` "
                         f"(allowed: {sorted(AGENT_MEMORY_KINDS)}; strategy documents use guidance/registry/elaboration)")
        ev = Path(a.evidence_file).read_text() if a.evidence_file else (a.evidence or "")
        mid = st.add_memory(a.kind, cx.worker, a.claim, ev, refs=a.refs)
        print(f"memory #{mid} added ({a.kind})")
        return
    rows = st.search_memory(a.kind, a.query, a.limit, a.since_round)
    for m in rows:
        print(f"--- #{m['id']} round {m['round']} {m['kind']} by {m['author']}")
        print(m["claim"])
        if m["evidence"] and not a.brief:
            print("evidence: " + m["evidence"])
        refs = json.loads(m["refs"] or "[]")
        if refs:
            print("refs: " + ", ".join(r[:12] for r in refs))
    if not rows:
        print("(no matching memory)")


def print_report(rep, file=None):
    file = file or sys.stdout
    pr = lambda *x: print(*x, file=file)
    pr(json.dumps({k: rep.get(k) for k in ("outcome", "fact_id", "submission_id", "reason", "seconds")}, indent=1))
    if rep.get("stderr_tail"):
        pr("build stderr (tail):\n" + rep["stderr_tail"])
    for c in rep.get("checks", []):
        if c.get("check") == "P1_code":
            pr(f"P1 code: n={c.get('n')} k={c.get('k')} d={c.get('d')} pass={c['pass']} {c.get('reason', '')}")
            continue
        p4 = c.get("P4_distance", {})
        cf = c.get("P3_code_flows", {})
        pr(f"{c['name']}: noiseless={c.get('P2_noiseless', {}).get('pass')} dem={c.get('P2_dem', {}).get('pass')} "
           f"flows={c.get('P3_flows', {}).get('pass')} logical-rank in/out={cf.get('logical_rank_inputs')}/"
           f"{cf.get('logical_rank_outputs')} of {cf.get('needed')} dist=[{p4.get('lb')},{p4.get('ub')}] "
           f"{p4.get('status')} claimed={p4.get('claimed')} FT={p4.get('fault_tolerant_at_instance')} pass={c['pass']}")
        if c.get("reason"):
            pr(f"    reason: {c['reason']}")
        for line in p4.get("witness_explained", [])[:10]:
            pr("    witness: " + line)
        for f, ok in (c.get("P3_flows", {}).get("signed") or {}).items():
            if not ok:
                pr(f"    flow FAILS: {f}   (unsigned holds: {c['P3_flows']['unsigned'].get(f)})")


def c_submit(cx, a, submit_hook=None):
    cx.need("worker", "human")
    if submit_hook is not None:          # service mode: run asynchronously
        return submit_hook(a)
    from gate import run_gate
    print_report(run_gate(Path(a.dir), cx.st, cx.worker, milp_time_s=a.milp_time))


def c_submission(cx, a):
    r = cx.st.submission(a.id)
    if r is None:
        print("(no such submission yet; if you just submitted, it may still be running)")
        return
    rep = json.loads(r["report"] or "{}")
    print_report(rep)
    print("\nfull report:\n" + json.dumps(rep, indent=1, default=str)[: a.max_chars])


def c_facts(cx, a):
    for r in cx.st.facts(None if a.all else "active"):
        print(fact_summary(r))


def c_fact(cx, a):
    r = cx.st.fact(a.id)
    if r is None:
        print("no unique fact with that id (give at least 8 hex characters)")
        return
    d = dict(r)
    for k in ("claims", "verdict", "depends_on", "review", "novelty"):
        d[k] = json.loads(d[k]) if d.get(k) else d.get(k)
    print(json.dumps(d, indent=1, default=str)[: a.max_chars])


def c_assign(cx, a):
    cx.need("main")
    ws = cx.st.get("workers_this_round")
    if ws and a.worker not in ws:
        raise ValueError(f"unknown worker {a.worker}; workers this round: {', '.join(ws)}")
    cx.st.assign(a.worker, read_text(a))
    print(f"assignment for {a.worker} recorded (round {cx.st.current_round()})")


def c_doc(cx, a):
    cx.need("main")
    mid = cx.st.add_memory(DOC_KINDS[a.cmd], cx.worker, read_text(a))
    print(f"{DOC_KINDS[a.cmd]} published as memory #{mid}")


def c_done(cx, a):
    cx.need("main", "human")
    cx.st.set("status", "done")
    cx.st.set("done_reason", a.reason)
    print("project marked done")


def c_revoke(cx, a):
    cx.need("main", "reviewer", "human")
    ids = cx.st.revoke(a.id, a.reason)
    cx.st.add_memory("review", cx.worker, f"revoked {len(ids)} fact(s): {', '.join(i[:12] for i in ids)}", a.reason)
    print("revoked: " + ", ".join(i[:12] for i in ids))


def c_review(cx, a):
    cx.need("reviewer", "human")
    text = read_text(a)
    cx.st.set_review(a.id, a.status, {"by": cx.worker, "text": text})
    cx.st.add_memory("review", cx.worker, f"review {a.status}: fact {a.id[:12]}", text, refs=[a.id])
    print("review recorded")


def c_novelty(cx, a):
    cx.need("novelty", "human")
    text = read_text(a)
    cx.st.set_novelty(a.id, a.status, {"by": cx.worker, "text": text})
    cx.st.add_memory("novelty", cx.worker, f"novelty {a.status}: fact {a.id[:12]}", text, refs=[a.id])
    print("novelty report recorded")


def c_sign_new(cx, a):
    cx.need("human")
    r = cx.st.fact(a.id)
    if r is None or r["novelty_status"] != "no_prior_found":
        raise ValueError("only a fact with novelty_status=no_prior_found can be signed as new")
    cx.st.con.execute("UPDATE facts SET novelty_status='human_confirmed_new' WHERE id=?", (r["id"],))
    cx.st.con.commit()
    print("signed as new")


def c_render(cx, a):
    from render import render_ledger
    print(f"ledger written to {render_ledger(cx.st)}")


def c_init(cx, a):
    cx.need("human")
    cx.st.set("task_path", str(Path(a.task).resolve()))
    cx.st.set("status", "open")
    cx.st.set("round", 0)
    cx.st.set("origin", a.origin)
    cx.st.set("noise_p", a.noise_p)
    print(f"project {cx.st.project} initialised at {cx.st.dir} (origin={a.origin}, noise p={a.noise_p})")


def parser():
    ap = argparse.ArgumentParser(prog="qec.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 exit_on_error=False)
    ap.add_argument("--project", default=os.environ.get("QEC_PROJECT"))
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("status"); p.add_argument("--full", action="store_true"); p.set_defaults(fn=c_status)
    sp.add_parser("task").set_defaults(fn=c_task)
    p = sp.add_parser("assignment"); p.add_argument("worker", nargs="?"); p.set_defaults(fn=c_assignment)
    p = sp.add_parser("memory"); msp = p.add_subparsers(dest="mem_cmd", required=True)
    x = msp.add_parser("add"); x.add_argument("--kind", required=True); x.add_argument("--claim", required=True)
    x.add_argument("--evidence"); x.add_argument("--evidence-file"); x.add_argument("--refs", nargs="*")
    x = msp.add_parser("search"); x.add_argument("--kind", nargs="*"); x.add_argument("--query")
    x.add_argument("--limit", type=int, default=20); x.add_argument("--since-round", type=int)
    x.add_argument("--brief", action="store_true")
    p.set_defaults(fn=c_memory)
    p = sp.add_parser("submit"); p.add_argument("dir"); p.add_argument("--milp-time", type=float, default=1800.0)
    p.add_argument("--wait", type=float, default=540.0); p.set_defaults(fn=c_submit)
    p = sp.add_parser("submission"); p.add_argument("id"); p.add_argument("--max-chars", type=int, default=12000)
    p.set_defaults(fn=c_submission)
    p = sp.add_parser("facts"); p.add_argument("--all", action="store_true"); p.set_defaults(fn=c_facts)
    p = sp.add_parser("fact"); p.add_argument("id"); p.add_argument("--max-chars", type=int, default=20000); p.set_defaults(fn=c_fact)
    p = sp.add_parser("assign"); p.add_argument("worker"); p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=c_assign)
    for name in DOC_KINDS:
        p = sp.add_parser(name); p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=c_doc)
    p = sp.add_parser("done"); p.add_argument("--reason", required=True); p.set_defaults(fn=c_done)
    p = sp.add_parser("revoke"); p.add_argument("id"); p.add_argument("--reason", required=True); p.set_defaults(fn=c_revoke)
    p = sp.add_parser("review"); p.add_argument("id"); p.add_argument("--status", required=True, choices=["ok", "flagged"])
    p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=c_review)
    p = sp.add_parser("novelty"); p.add_argument("id")
    p.add_argument("--status", required=True, choices=["prior_found", "no_prior_found"])
    p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=c_novelty)
    p = sp.add_parser("sign-new"); p.add_argument("id"); p.set_defaults(fn=c_sign_new)
    sp.add_parser("render").set_defaults(fn=c_render)
    p = sp.add_parser("init"); p.add_argument("--task", required=True)
    p.add_argument("--origin", default="agent", choices=["agent", "calibration"]); p.add_argument("--noise-p", type=float, default=1e-3)
    p.set_defaults(fn=c_init)
    return ap


PATH_ARGS = ("file", "evidence_file", "dir")


def _confine_paths(a, cwd, allowed_roots):
    """Make path arguments absolute (relative to the caller's cwd) and require that
    they lie inside one of the caller's allowed directories."""
    for name in PATH_ARGS:
        v = getattr(a, name, None)
        if not v:
            continue
        p = Path(v)
        if not p.is_absolute():
            p = Path(cwd or "/") / p
        p = p.resolve()
        if allowed_roots is not None and not any(p == Path(r).resolve() or Path(r).resolve() in p.parents
                                                 for r in allowed_roots):
            raise Denied(f"path {v} is outside your working directory")
        setattr(a, name, str(p))


def dispatch(argv, project, role, worker, submit_hook=None, cwd=None, allowed_roots=None) -> tuple[str, int]:
    """Run one command with a fixed role; return (output text, exit code). Used by the service."""
    buf = io.StringIO()
    code = 0
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            a = parser().parse_args(argv)
            _confine_paths(a, cwd, allowed_roots)
            cx = Ctx(project, role, worker)
            if a.fn is c_submit:
                r = c_submit(cx, a, submit_hook)
                if isinstance(r, str):
                    print(r)
            else:
                a.fn(cx, a)
        except SystemExit as e:
            code = int(e.code or 0)
        except Denied as e:
            print(str(e)); code = 13
        except Exception as e:
            print(f"error: {type(e).__name__}: {e}"); code = 1
    return buf.getvalue(), code


def client(sock_path: str, argv) -> int:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)
    s.sendall((json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n").encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    resp = json.loads(data.decode())
    sys.stdout.write(resp["out"])
    return resp["code"]


def main():
    sock = os.environ.get("QEC_SOCKET")
    if sock:
        sys.exit(client(sock, sys.argv[1:]))
    a0 = parser()
    a = a0.parse_args()
    if not a.project:
        sys.exit("no project: pass --project or set QEC_PROJECT")
    out, code = dispatch(sys.argv[1:], a.project, "human", "human", cwd=os.getcwd())
    sys.stdout.write(out)
    sys.exit(code)


if __name__ == "__main__":
    main()
