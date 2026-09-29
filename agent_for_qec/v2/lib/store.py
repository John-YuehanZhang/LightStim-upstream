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

MEMORY_KINDS = {
    # written by workers
    "finding", "example", "counterexample", "dead_end", "obstacle", "direction", "plan",
    # written by main agent
    "elaboration", "guidance", "route_registry",
    # written automatically / by other roles
    "verification", "review", "novelty", "lesson", "operator",
}

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
  origin TEXT DEFAULT 'agent'            -- agent | calibration | human
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
  exit INTEGER, cost_usd REAL, turns INTEGER, result_head TEXT
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
        self.con = sqlite3.connect(self.db_path, timeout=60)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

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
                 depends_on: List[str], submission_id: str, author: str, origin: str = "agent") -> None:
        for dep in depends_on:
            r = self.fact(dep)
            if r is None or r["status"] != "active":
                raise ValueError(f"dependency {dep} is missing or revoked")
        self.con.execute(
            "INSERT OR REPLACE INTO facts(id,kind,title,claims,verdict,depends_on,submission_id,author,created,origin)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fact_id, kind, title, jdump(claims), jdump(verdict), jdump(depends_on),
             submission_id, author, time.time(), origin))
        self.con.commit()

    def fact(self, fact_id: str) -> Optional[sqlite3.Row]:
        rows = self.con.execute("SELECT * FROM facts WHERE id LIKE ?", (fact_id + "%",)).fetchall()
        return rows[0] if len(rows) == 1 else None

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
        self.con.execute("UPDATE facts SET review_status=?, review=? WHERE id=?",
                         (status, jdump(review), self.fact(fact_id)["id"]))
        self.con.commit()

    def set_novelty(self, fact_id: str, status: str, novelty: dict) -> None:
        if status == "human_confirmed_new":
            raise ValueError("only the human operator may set human_confirmed_new (use qec.py sign-new)")
        self.con.execute("UPDATE facts SET novelty_status=?, novelty=? WHERE id=?",
                         (status, jdump(novelty), self.fact(fact_id)["id"]))
        self.con.commit()

    # ------------------------------------------------------------ submissions
    def add_submission(self, sub_id: str, author: str, path: str, outcome: str, report: dict,
                       fact_id: Optional[str]) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO submissions(id,author,created,path,outcome,report,fact_id) VALUES(?,?,?,?,?,?,?)",
            (sub_id, author, time.time(), path, outcome, jdump(report), fact_id))
        self.con.commit()

    # ------------------------------------------------------------ assignments
    def assign(self, worker: str, text: str) -> None:
        rnd = self.current_round()
        self.con.execute("UPDATE assignments SET status='superseded' WHERE worker=? AND status='open'", (worker,))
        self.con.execute("INSERT INTO assignments(round,worker,text,created) VALUES(?,?,?,?)",
                         (rnd, worker, text, time.time()))
        self.con.commit()

    def open_assignments(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM assignments WHERE status='open' ORDER BY worker").fetchall()

    def assignment(self, worker: str) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM assignments WHERE worker=? AND status='open' ORDER BY id DESC LIMIT 1",
                                (worker,)).fetchone()

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
