#!/usr/bin/env python3
"""qec.py -- the single command-line interface agents use to touch shared state.

Role gating: the launcher sets QEC_ROLE (main | worker | reviewer | novelty |
human) and QEC_PROJECT / QEC_WORKER. Commands that change shared state check the
role (after Danus: permissions by construction, not by prompt).

  status                      project overview (task, facts, open assignments, recent memory)
  task                        print the task statement
  memory add ...              publish a typed memory entry
  memory search ...           search shared memory
  submit DIR                  run the verification gate on a submission (worker, human)
  facts [--all]               list facts;  fact ID  show one fact
  assign WORKER --file F      (main) set a worker's assignment for the next round
  guidance|elaboration|registry --file F   (main) publish strategy documents
  done --reason R             (main) declare the task complete
  revoke ID --reason R        (main, reviewer, human) revoke a fact and its dependants
  review ID --status ok|flagged --file F     (reviewer)
  novelty ID --status prior_found|no_prior_found --file F   (novelty)
  sign-new ID                 (human) confirm a fact as new after reading the novelty report
  render                      write results/<project>/LEDGER.md from the store
  init --task FILE            (human) create a project
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

V2 = Path(__file__).resolve().parent
sys.path.insert(0, str(V2 / "lib"))
from store import Store  # noqa: E402

ROLE = os.environ.get("QEC_ROLE", "human")
WORKER = os.environ.get("QEC_WORKER", ROLE)


def need(*roles):
    if ROLE not in roles:
        sys.exit(f"permission denied: role '{ROLE}' may not run this command (allowed: {', '.join(roles)})")


def read_text(args) -> str:
    if getattr(args, "file", None):
        return Path(args.file).read_text()
    if getattr(args, "text", None):
        return args.text
    sys.exit("give --file or --text")


def short(s, n=160):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def fact_summary(r) -> str:
    v = json.loads(r["verdict"] or "{}")
    code = next((c for c in v.get("checks", []) if c.get("check") == "P1_code"), None)
    circ = [c for c in v.get("checks", []) if c.get("check") == "P2-P4_circuit"]
    cs = ", ".join(f"{c['name']}: dist={c.get('P4_distance', {}).get('lb')}"
                   f"{'' if c.get('P4_distance', {}).get('status') == 'exact' else '+'}"
                   f"{' FT' if c.get('P4_distance', {}).get('fault_tolerant_at_instance') else ''}" for c in circ)
    cd = f"[[{code['n']},{code['k']},{code['d']}]]" if code else "-"
    return (f"{r['id'][:12]}  {r['kind']:<10} {cd:<14} {short(r['title'], 60):<60}  {cs}  "
            f"review={r['review_status']} novelty={r['novelty_status']} origin={r['origin']}"
            + (f"  REVOKED: {short(r['revoked_reason'], 60)}" if r["status"] == "revoked" else ""))


def cmd_status(st: Store, args):
    print(f"# project {st.project}   round {st.current_round()}   status {st.get('status', 'open')}")
    print(f"# you are role={ROLE} worker={WORKER}\n")
    task = st.get("task_path")
    if task:
        print(f"task statement: {task}  (run `qec.py task` to print it)\n")
    facts = st.facts()
    print(f"## verified facts ({len(facts)} active)")
    for r in facts:
        print("  " + fact_summary(r))
    rev = st.facts("revoked")
    if rev:
        print(f"## revoked facts ({len(rev)})")
        for r in rev:
            print("  " + fact_summary(r))
    print("\n## open assignments")
    for a in st.open_assignments():
        print(f"  {a['worker']} (round {a['round']}): {short(a['text'], 300)}")
    for kind in ("guidance", "route_registry"):
        m = st.search_memory([kind], limit=1)
        if m:
            print(f"\n## latest {kind} (round {m[0]['round']}, id {m[0]['id']})\n" + textwrap.indent(m[0]["claim"], "  "))
    n = 12 if not args.full else 60
    print(f"\n## recent shared memory (last {n})")
    for m in st.search_memory(limit=n):
        if m["kind"] in ("guidance", "route_registry", "elaboration"):
            continue
        print(f"  #{m['id']} r{m['round']} {m['kind']:<14} {m['author']:<10} {short(m['claim'], 110)}")


def cmd_memory(st: Store, args):
    if args.mem_cmd == "add":
        if args.kind in ("guidance", "elaboration", "route_registry"):
            need("main")
        if args.kind in ("verification",):
            sys.exit("verification entries are written by the gate only")
        ev = Path(args.evidence_file).read_text() if args.evidence_file else (args.evidence or "")
        mid = st.add_memory(args.kind, WORKER, args.claim, ev, refs=args.refs)
        print(f"memory #{mid} added ({args.kind})")
    else:
        rows = st.search_memory(args.kind, args.query, args.limit, args.since_round)
        for m in rows:
            print(f"--- #{m['id']} round {m['round']} {m['kind']} by {m['author']}")
            print(m["claim"])
            if m["evidence"] and not args.brief:
                print("evidence: " + m["evidence"])
            refs = json.loads(m["refs"] or "[]")
            if refs:
                print("refs: " + ", ".join(refs))
        if not rows:
            print("(no matching memory)")


def cmd_submit(st: Store, args):
    need("worker", "human")
    from gate import run_gate
    rep = run_gate(Path(args.dir), st, WORKER, milp_time_s=args.milp_time)
    print(json.dumps({k: rep.get(k) for k in ("outcome", "fact_id", "reason", "seconds")}, indent=1))
    for c in rep.get("checks", []):
        if c.get("check") == "P1_code":
            print(f"P1 code: n={c.get('n')} k={c.get('k')} d={c.get('d')} pass={c['pass']} {c.get('reason', '')}")
        else:
            p4 = c.get("P4_distance", {})
            print(f"{c['name']}: noiseless={c.get('P2_noiseless', {}).get('pass')} dem={c.get('P2_dem', {}).get('pass')} "
                  f"flows={c.get('P3_flows', {}).get('pass')} dist=[{p4.get('lb')},{p4.get('ub')}] {p4.get('status')} "
                  f"claimed={p4.get('claimed')} FT={p4.get('fault_tolerant_at_instance')} pass={c['pass']}")
            for line in p4.get("witness_explained", [])[:10]:
                print("    witness: " + line)
            if c.get("P3_flows", {}).get("signed"):
                for f, ok in c["P3_flows"]["signed"].items():
                    if not ok:
                        print(f"    flow FAILS: {f}   (unsigned holds: {c['P3_flows']['unsigned'].get(f)})")


def cmd_facts(st: Store, args):
    for r in st.facts(None if args.all else "active"):
        print(fact_summary(r))


def cmd_fact(st: Store, args):
    r = st.fact(args.id)
    if r is None:
        sys.exit("no unique fact with that id prefix")
    d = dict(r)
    for k in ("claims", "verdict", "depends_on", "review", "novelty"):
        d[k] = json.loads(d[k]) if d.get(k) else d.get(k)
    print(json.dumps(d, indent=1, default=str)[: args.max_chars])


def cmd_assign(st: Store, args):
    need("main")
    st.assign(args.worker, read_text(args))
    print(f"assignment for {args.worker} recorded (round {st.current_round()})")


def cmd_doc(kind):
    def f(st: Store, args):
        need("main")
        mid = st.add_memory(kind, WORKER, read_text(args))
        print(f"{kind} published as memory #{mid}")
    return f


def cmd_done(st: Store, args):
    need("main", "human")
    st.set("status", "done")
    st.set("done_reason", args.reason)
    print("project marked done")


def cmd_revoke(st: Store, args):
    need("main", "reviewer", "human")
    ids = st.revoke(args.id, args.reason)
    st.add_memory("review", WORKER, f"revoked {len(ids)} fact(s): {', '.join(i[:12] for i in ids)}", args.reason)
    print("revoked: " + ", ".join(i[:12] for i in ids))


def cmd_review(st: Store, args):
    need("reviewer", "human")
    text = read_text(args)
    st.set_review(args.id, args.status, {"by": WORKER, "text": text})
    st.add_memory("review", WORKER, f"review {args.status}: fact {args.id[:12]}", text, refs=[args.id])
    print("review recorded")


def cmd_novelty(st: Store, args):
    need("novelty", "human")
    text = read_text(args)
    st.set_novelty(args.id, args.status, {"by": WORKER, "text": text})
    st.add_memory("novelty", WORKER, f"novelty {args.status}: fact {args.id[:12]}", text, refs=[args.id])
    print("novelty report recorded")


def cmd_sign_new(st: Store, args):
    need("human")
    r = st.fact(args.id)
    if r["novelty_status"] != "no_prior_found":
        sys.exit("only facts with novelty_status=no_prior_found can be signed as new")
    st.con.execute("UPDATE facts SET novelty_status='human_confirmed_new' WHERE id=?", (r["id"],))
    st.con.commit()
    print("signed as new")


def cmd_render(st: Store, args):
    from render import render_ledger
    p = render_ledger(st)
    print(f"ledger written to {p}")


def cmd_task(st: Store, args):
    p = st.get("task_path")
    print(Path(p).read_text() if p else "(no task)")


def cmd_init(st: Store, args):
    need("human")
    st.set("task_path", str(Path(args.task).resolve()))
    st.set("status", "open")
    st.set("round", 0)
    print(f"project {st.project} initialised at {st.dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("QEC_PROJECT"))
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("status"); p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_status)
    sp.add_parser("task").set_defaults(fn=cmd_task)
    p = sp.add_parser("memory"); msp = p.add_subparsers(dest="mem_cmd", required=True)
    a = msp.add_parser("add"); a.add_argument("--kind", required=True); a.add_argument("--claim", required=True)
    a.add_argument("--evidence"); a.add_argument("--evidence-file"); a.add_argument("--refs", nargs="*")
    s = msp.add_parser("search"); s.add_argument("--kind", nargs="*"); s.add_argument("--query")
    s.add_argument("--limit", type=int, default=20); s.add_argument("--since-round", type=int); s.add_argument("--brief", action="store_true")
    p.set_defaults(fn=cmd_memory)
    p = sp.add_parser("submit"); p.add_argument("dir"); p.add_argument("--milp-time", type=float, default=1800.0); p.set_defaults(fn=cmd_submit)
    p = sp.add_parser("facts"); p.add_argument("--all", action="store_true"); p.set_defaults(fn=cmd_facts)
    p = sp.add_parser("fact"); p.add_argument("id"); p.add_argument("--max-chars", type=int, default=20000); p.set_defaults(fn=cmd_fact)
    p = sp.add_parser("assign"); p.add_argument("worker"); p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=cmd_assign)
    for kind, name in (("guidance", "guidance"), ("elaboration", "elaboration"), ("route_registry", "registry")):
        p = sp.add_parser(name); p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=cmd_doc(kind))
    p = sp.add_parser("done"); p.add_argument("--reason", required=True); p.set_defaults(fn=cmd_done)
    p = sp.add_parser("revoke"); p.add_argument("id"); p.add_argument("--reason", required=True); p.set_defaults(fn=cmd_revoke)
    p = sp.add_parser("review"); p.add_argument("id"); p.add_argument("--status", required=True, choices=["ok", "flagged"])
    p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=cmd_review)
    p = sp.add_parser("novelty"); p.add_argument("id"); p.add_argument("--status", required=True, choices=["prior_found", "no_prior_found"])
    p.add_argument("--file"); p.add_argument("--text"); p.set_defaults(fn=cmd_novelty)
    p = sp.add_parser("sign-new"); p.add_argument("id"); p.set_defaults(fn=cmd_sign_new)
    sp.add_parser("render").set_defaults(fn=cmd_render)
    p = sp.add_parser("init"); p.add_argument("--task", required=True); p.set_defaults(fn=cmd_init)
    args = ap.parse_args()
    if not args.project:
        sys.exit("no project: pass --project or set QEC_PROJECT")
    args.fn(Store(args.project), args)


if __name__ == "__main__":
    main()
