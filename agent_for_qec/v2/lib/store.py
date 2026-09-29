"""Shared store for agent-for-QEC v2 (SQLite).

Three memory tiers (after Danus, arXiv 2607.06447):
  * worker-local notes      -> plain files in the worker directory (not here)
  * shared memory           -> table `memory`, typed entries (awareness, never truth)
  * fact graph              -> table `facts`, only written by the verification gate

A fact is content-addressed: its id hashes the submission bundle (build.py +
submission.json) together with the ids of the facts it depends on. Revoking a
fact revokes everything that depends on it, transitively.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

RUNTIME_ROOT = Path(os.environ.get("QEC_RUNTIME_ROOT", "/nvme2n1/yuehan_zhang/agent_for_qec_runtime"))
SYSTEM_VERSION = "2.2"

MEMORY_KINDS = {
    # written by workers
    "finding", "example", "counterexample", "dead_end", "obstacle", "direction", "plan",
    # written by main agent
    "elaboration", "guidance", "route_registry",
    # written automatically / by other roles
    "verification", "review", "novelty", "lesson", "operator",
    # written by the orchestrator only: run conditions the agents must follow
    "operating_rules",
    # written by refuters
    "refutation",
}

# aggregated refutation state of a fact
REFUTE_STATES = ("unchallenged", "challenged", "no_problem_found", "doubtful", "refuted_pending_human",
                 "refutation_rejected", "refutation_upheld")

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  kind TEXT, title TEXT,
  claims TEXT,            -- JSON: what the submission claimed
  verdict TEXT,           -- JSON: the gate's full report
  depends_on TEXT,        -- JSON list of fact ids
  submission_id TEXT,
  author TEXT, created REAL,
  status TEXT DEFAULT 'active',        -- active | revoked
  revoked_reason TEXT,
  review_status TEXT DEFAULT 'pending',  -- pending | ok | flagged
  review TEXT,
  novelty_status TEXT DEFAULT 'pending', -- pending | prior_found | no_prior_found | human_confirmed_new
  novelty TEXT,
  origin TEXT DEFAULT 'agent',           -- agent | calibration | human
  refute_status TEXT DEFAULT 'unchallenged',
  run_id INTEGER,                        -- the process that submitted it (cost, model, account)
  evaluation TEXT                        -- JSON: final evaluation (logical error rates), filled after the run
);
CREATE TABLE IF NOT EXISTS challenges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round INTEGER, fact_id TEXT, n INTEGER, focus TEXT, created REAL,
  status TEXT DEFAULT 'open'             -- open | done
);
CREATE TABLE IF NOT EXISTS refutations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  challenge_id INTEGER, fact_id TEXT, refuter TEXT, verdict TEXT, text TEXT, evidence TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS submissions (
  id TEXT PRIMARY KEY,
  author TEXT, created REAL, path TEXT,
  outcome TEXT,           -- accepted | rejected | bounds_only | error
  report TEXT,            -- JSON
  fact_id TEXT
);
CREATE TABLE IF NOT EXISTS memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT, author TEXT, created REAL, round INTEGER,
  claim TEXT, evidence TEXT, refs TEXT, extra TEXT
);
CREATE TABLE IF NOT EXISTS assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round INTEGER, worker TEXT, text TEXT, created REAL, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round INTEGER, role TEXT, worker TEXT, account TEXT, provider TEXT, model TEXT,
  prompt_sha TEXT, repo_head TEXT, log TEXT, started REAL, ended REAL,
  exit INTEGER, cost_usd REAL, turns INTEGER, result_head TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_creation_tokens INTEGER
);
"""


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def jdump(x) -> str:
    return json.dumps(x, default=_default)


def project_dir(project: str) -> Path:
    d = RUNTIME_ROOT / project
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, project: str):
        self.project = project
        self.dir = project_dir(project)
        self.db_path = self.dir / "store.sqlite"
        self.con = sqlite3.connect(self.db_path, timeout=300)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self._migrate("facts", {"refute_status": "TEXT DEFAULT 'unchallenged'", "run_id": "INTEGER",
                                "evaluation": "TEXT"})
        self._migrate("runs", {"input_tokens": "INTEGER", "output_tokens": "INTEGER", "cache_read_tokens": "INTEGER",
                               "cache_creation_tokens": "INTEGER"})
        self.con.commit()

    def _migrate(self, table: str, columns: Dict[str, str]) -> None:
        have = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
        for c, t in columns.items():
            if c not in have:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")

    # ------------------------------------------------------------ project kv
    def get(self, key: str, default: Any = None) -> Any:
        r = self.con.execute("SELECT value FROM project WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set(self, key: str, value: Any) -> None:
        self.con.execute("INSERT OR REPLACE INTO project(key,value) VALUES(?,?)", (key, jdump(value)))
        self.con.commit()

    def current_round(self) -> int:
        return int(self.get("round", 0))

    # ------------------------------------------------------------ memory
    def add_memory(self, kind: str, author: str, claim: str, evidence: str = "",
                   refs: Optional[List[str]] = None, extra: Optional[dict] = None) -> int:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind {kind!r}; allowed: {sorted(MEMORY_KINDS)}")
        if not claim or not claim.strip():
            raise ValueError("empty claim")
        cur = self.con.execute(
            "INSERT INTO memory(kind,author,created,round,claim,evidence,refs,extra) VALUES(?,?,?,?,?,?,?,?)",
            (kind, author, time.time(), self.current_round(), claim, evidence,
             jdump(refs or []), jdump(extra or {})))
        self.con.commit()
        return cur.lastrowid

    def search_memory(self, kinds: Optional[Iterable[str]] = None, query: Optional[str] = None,
                      limit: int = 50, since_round: Optional[int] = None) -> List[sqlite3.Row]:
        sql, args = "SELECT * FROM memory WHERE 1=1", []
        if kinds:
            kinds = list(kinds)
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if query:
            for word in query.split():
                sql += " AND (claim LIKE ? OR evidence LIKE ?)"
                args += [f"%{word}%", f"%{word}%"]
        if since_round is not None:
            sql += " AND round >= ?"
            args.append(since_round)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self.con.execute(sql, args).fetchall()

    # ------------------------------------------------------------ facts
    def add_fact(self, fact_id: str, kind: str, title: str, claims: dict, verdict: dict,
                 depends_on: List[str], submission_id: str, author: str, origin: str = "agent",
                 run_id: Optional[int] = None) -> None:
        """Insert a new fact. Never overwrites: an existing id (active or revoked) is an error."""
        for dep in depends_on:
            r = self.fact(dep, exact=True)
            if r is None or r["status"] != "active":
                raise ValueError(f"dependency {dep} is missing or revoked")
        with self.con:
            self.con.execute(
                "INSERT INTO facts(id,kind,title,claims,verdict,depends_on,submission_id,author,created,origin,run_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (fact_id, kind, title, jdump(claims), jdump(verdict), jdump(depends_on),
                 submission_id, author, time.time(), origin, run_id))

    def set_evaluation(self, fact_id: str, evaluation: dict) -> None:
        fid = self.resolve_fact_id(fact_id)
        self.con.execute("UPDATE facts SET evaluation=? WHERE id=?", (jdump(evaluation), fid))
        self.con.commit()

    def run(self, run_id) -> Optional[sqlite3.Row]:
        if run_id is None:
            return None
        return self.con.execute("SELECT * FROM runs WHERE id=?", (int(run_id),)).fetchone()

    def cost_summary(self) -> dict:
        r = self.con.execute("SELECT COUNT(*) n, SUM(cost_usd) usd, SUM(input_tokens) i, SUM(output_tokens) o, "
                             "SUM(cache_read_tokens) cr, SUM(cache_creation_tokens) cc, "
                             "SUM(COALESCE(ended, started) - started) secs FROM runs").fetchone()
        per_role = self.con.execute("SELECT role, COUNT(*) n, SUM(cost_usd) usd, COUNT(DISTINCT worker) names "
                                    "FROM runs GROUP BY role").fetchall()
        return {"processes": r["n"], "usd": r["usd"] or 0, "input_tokens": r["i"] or 0, "output_tokens": r["o"] or 0,
                "cache_read_tokens": r["cr"] or 0, "cache_creation_tokens": r["cc"] or 0,
                "process_seconds": r["secs"] or 0,
                "per_role": {x["role"]: {"processes": x["n"], "usd": x["usd"] or 0, "distinct_names": x["names"]}
                             for x in per_role}}

    def fact(self, fact_id: str, exact: bool = False) -> Optional[sqlite3.Row]:
        """Look up a fact by full id, or by an unambiguous prefix of at least 8 hex characters."""
        if not fact_id or any(c not in "0123456789abcdef" for c in fact_id.lower()):
            return None
        if exact or len(fact_id) == 64:
            return self.con.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        if len(fact_id) < 8:
            return None
        rows = self.con.execute("SELECT * FROM facts WHERE substr(id,1,?)=?", (len(fact_id), fact_id.lower())).fetchall()
        return rows[0] if len(rows) == 1 else None

    def resolve_fact_id(self, ref: str) -> str:
        r = self.fact(ref)
        if r is None:
            raise ValueError(f"'{ref}' does not identify exactly one fact (use at least 8 hex characters)")
        if r["status"] != "active":
            raise ValueError(f"fact {r['id'][:12]} is revoked")
        return r["id"]

    def facts(self, status: Optional[str] = "active") -> List[sqlite3.Row]:
        if status is None:
            return self.con.execute("SELECT * FROM facts ORDER BY created").fetchall()
        return self.con.execute("SELECT * FROM facts WHERE status=? ORDER BY created", (status,)).fetchall()

    def revoke(self, fact_id: str, reason: str) -> List[str]:
        """Revoke a fact and all facts depending on it (transitively)."""
        root = self.fact(fact_id)
        if root is None:
            raise ValueError(f"no unique fact matches {fact_id}")
        todo, done = [root["id"]], []
        while todo:
            fid = todo.pop()
            if fid in done:
                continue
            done.append(fid)
            self.con.execute("UPDATE facts SET status='revoked', revoked_reason=? WHERE id=?",
                             (reason if fid == root["id"] else f"depends on revoked {root['id'][:12]}: {reason}", fid))
            for r in self.con.execute("SELECT id, depends_on FROM facts WHERE status='active'").fetchall():
                if fid in json.loads(r["depends_on"] or "[]"):
                    todo.append(r["id"])
        self.con.commit()
        return done

    def set_review(self, fact_id: str, status: str, review: dict) -> None:
        fid = self.resolve_fact_id(fact_id)
        self.con.execute("UPDATE facts SET review_status=?, review=? WHERE id=?", (status, jdump(review), fid))
        self.con.commit()

    def set_novelty(self, fact_id: str, status: str, novelty: dict) -> None:
        if status == "human_confirmed_new":
            raise ValueError("only the human operator may set human_confirmed_new (use qec.py sign-new)")
        fid = self.resolve_fact_id(fact_id)
        self.con.execute("UPDATE facts SET novelty_status=?, novelty=? WHERE id=?", (status, jdump(novelty), fid))
        self.con.commit()

    # ------------------------------------------------------------ submissions
    def add_submission(self, sub_id: str, author: str, path: str, outcome: str, report: dict,
                       fact_id: Optional[str]) -> None:
        self.con.execute(
            "INSERT INTO submissions(id,author,created,path,outcome,report,fact_id) VALUES(?,?,?,?,?,?,?)",
            (f"{sub_id}:{time.time():.6f}", author, time.time(), path, outcome, jdump(report), fact_id))
        self.con.commit()

    def submission(self, ref: str) -> Optional[sqlite3.Row]:
        rows = self.con.execute("SELECT * FROM submissions WHERE substr(id,1,?)=? ORDER BY created DESC",
                                (len(ref), ref)).fetchall()
        return rows[0] if rows else None

    # ------------------------------------------------------------ assignments
    def assign(self, worker: str, text: str) -> None:
        rnd = self.current_round()
        self.con.execute("UPDATE assignments SET status='superseded' WHERE worker=? AND status='open'", (worker,))
        self.con.execute("INSERT INTO assignments(round,worker,text,created) VALUES(?,?,?,?)",
                         (rnd, worker, text, time.time()))
        self.con.commit()

    def open_assignments(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM assignments WHERE status='open' ORDER BY worker").fetchall()

    def close_assignments(self, status: str, worker: Optional[str] = None) -> int:
        """Set the status of open assignments (all, or one worker's): done | abandoned."""
        q, args = "UPDATE assignments SET status=? WHERE status='open'", [status]
        if worker is not None:
            q, args = q + " AND worker=?", args + [worker]
        n = self.con.execute(q, args).rowcount
        self.con.commit()
        return n

    def assignment(self, worker: str) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM assignments WHERE worker=? AND status='open' ORDER BY id DESC LIMIT 1",
                                (worker,)).fetchone()

    # ------------------------------------------------------------ operating rules
    def set_operating_rules(self, scope: str, text: str) -> int:
        """Orchestrator only. scope 'all' applies to every process of the round,
        otherwise it is the name of one process (worker/refuter/main/...)."""
        return self.add_memory("operating_rules", "operator", text, extra={"scope": scope})

    def operating_rules_for(self, name: str) -> List[sqlite3.Row]:
        """Latest round-wide rules and latest rules addressed to `name`."""
        out = []
        for scope in ("all", name):
            r = self.con.execute("SELECT * FROM memory WHERE kind='operating_rules' AND json_extract(extra,'$.scope')=?"
                                 " ORDER BY id DESC LIMIT 1", (scope,)).fetchone()
            if r is not None:
                out.append(r)
        return out

    # ------------------------------------------------------------ challenges / refutations
    def add_challenge(self, fact_id: str, n: int, focus: str) -> int:
        fid = self.resolve_fact_id(fact_id)
        if not 1 <= n <= 5:
            raise ValueError("number of refuters must be between 1 and 5")
        cur = self.con.execute("INSERT INTO challenges(round,fact_id,n,focus,created) VALUES(?,?,?,?,?)",
                               (self.current_round(), fid, n, focus, time.time()))
        self.con.execute("UPDATE facts SET refute_status='challenged' WHERE id=? AND refute_status='unchallenged'",
                         (fid,))
        self.con.commit()
        return cur.lastrowid

    def challenge(self, cid: int) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM challenges WHERE id=?", (int(cid),)).fetchone()

    def open_challenges(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM challenges WHERE status='open' ORDER BY id").fetchall()

    def close_challenge(self, cid: int) -> None:
        self.con.execute("UPDATE challenges SET status='done' WHERE id=?", (int(cid),))
        self.con.commit()

    def add_refutation(self, cid: int, refuter: str, verdict: str, text: str, evidence: str) -> None:
        if verdict not in ("refuted", "doubtful", "no_problem_found"):
            raise ValueError("verdict must be refuted | doubtful | no_problem_found")
        ch = self.challenge(cid)
        if ch is None:
            raise ValueError(f"no challenge {cid}")
        if verdict == "refuted" and not (evidence or "").strip():
            raise ValueError("a refutation needs reproducible evidence (--evidence-file with the script and its output)")
        if self.con.execute("SELECT 1 FROM refutations WHERE challenge_id=? AND refuter=?", (cid, refuter)).fetchone():
            raise ValueError("you have already recorded a verdict for this challenge")
        self.con.execute("INSERT INTO refutations(challenge_id,fact_id,refuter,verdict,text,evidence,created)"
                         " VALUES(?,?,?,?,?,?,?)", (cid, ch["fact_id"], refuter, verdict, text, evidence, time.time()))
        self.con.commit()
        self._aggregate_refute(ch["fact_id"])

    def refutations(self, fact_id: str) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM refutations WHERE fact_id=? ORDER BY id", (fact_id,)).fetchall()

    def _aggregate_refute(self, fact_id: str) -> None:
        r = self.fact(fact_id, exact=True)
        if r is None or r["refute_status"] in ("refutation_rejected", "refutation_upheld"):
            return
        vs = [x["verdict"] for x in self.refutations(fact_id)]
        st = ("refuted_pending_human" if "refuted" in vs else "doubtful" if "doubtful" in vs
              else "no_problem_found" if vs else r["refute_status"])
        self.con.execute("UPDATE facts SET refute_status=? WHERE id=?", (st, fact_id))
        self.con.commit()

    def adjudicate(self, fact_id: str, uphold: bool, reason: str) -> None:
        """Human decision on a refuted fact: uphold the refutation (fact revoked) or reject it."""
        r = self.fact(fact_id)
        if r is None or r["refute_status"] not in ("refuted_pending_human", "doubtful"):
            raise ValueError("only a refuted or doubtful fact can be adjudicated")
        self.con.execute("UPDATE facts SET refute_status=? WHERE id=?",
                         ("refutation_upheld" if uphold else "refutation_rejected", r["id"]))
        self.con.commit()
        if uphold:
            self.revoke(r["id"], f"refutation upheld by human: {reason}")

    # ------------------------------------------------------------ runs
    def add_run(self, **kw) -> int:
        cols = ",".join(kw)
        cur = self.con.execute(f"INSERT INTO runs({cols}) VALUES({','.join('?' * len(kw))})", tuple(kw.values()))
        self.con.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, **kw) -> None:
        sets = ",".join(f"{k}=?" for k in kw)
        self.con.execute(f"UPDATE runs SET {sets} WHERE id=?", (*kw.values(), run_id))
        self.con.commit()
