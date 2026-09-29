"""Shared results library across projects (one per runtime root).

Every project keeps its own store; the library is a read model rebuilt from all
project stores (`sync`) plus the portfolio's topic table, which the planner
writes. Agents reach it only through `qec.py library ...` (service side).

  lib_facts   one row per fact of every project (status, refutation, novelty)
  lib_notes   findings, dead ends, obstacles and counterexamples of every project
  topics      the planner's projects: layer, task text, workers, rounds, status

Facts of other projects may be used as dependencies by writing
`"external_depends_on": ["<project>/<fact id>"]` in submission.json; when such a
source fact is revoked, `propagate_revocations` revokes the dependants.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from store import RUNTIME_ROOT, Store, jdump

SCHEMA = """
CREATE TABLE IF NOT EXISTS lib_facts (
  id TEXT, project TEXT, kind TEXT, title TEXT, n INTEGER, k INTEGER, d INTEGER, layer TEXT,
  status TEXT, refute_status TEXT, novelty_status TEXT, description TEXT, external_deps TEXT, created REAL,
  PRIMARY KEY (project, id)
);
CREATE TABLE IF NOT EXISTS lib_notes (
  project TEXT, memory_id INTEGER, kind TEXT, author TEXT, round INTEGER, claim TEXT, evidence TEXT, created REAL,
  PRIMARY KEY (project, memory_id)
);
CREATE TABLE IF NOT EXISTS topics (
  name TEXT PRIMARY KEY, portfolio TEXT, layer TEXT, title TEXT, task TEXT, rationale TEXT,
  workers INTEGER, rounds INTEGER, status TEXT DEFAULT 'proposed',   -- proposed | running | done | closed | stopped
  created REAL, updated REAL, reason TEXT
);
"""
NOTE_KINDS = ("finding", "dead_end", "obstacle", "counterexample")


class Library:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or RUNTIME_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.root / "library.sqlite", timeout=300)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.con.commit()

    # ------------------------------------------------------------ sync from project stores
    def projects(self) -> List[str]:
        return sorted(p.parent.name for p in self.root.glob("*/store.sqlite"))

    def sync(self) -> None:
        for name in self.projects():
            st = Store(name)
            if st.get("library") is False:          # e.g. portfolio stores, test projects
                continue
            for f in st.facts(None):
                v = json.loads(f["verdict"] or "{}")
                code = next((c for c in v.get("checks", []) if c.get("check") == "P1_code"), {})
                claims = json.loads(f["claims"] or "{}")
                self.con.execute(
                    "INSERT OR REPLACE INTO lib_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f["id"], name, f["kind"], f["title"], code.get("n"), code.get("k"), code.get("d"),
                     str(claims.get("layer") or ""), f["status"], f["refute_status"], f["novelty_status"],
                     claims.get("description"), jdump(claims.get("external_depends_on") or []), f["created"]))
            for m in st.search_memory(list(NOTE_KINDS), limit=100000):
                self.con.execute("INSERT OR REPLACE INTO lib_notes VALUES(?,?,?,?,?,?,?,?)",
                                 (name, m["id"], m["kind"], m["author"], m["round"], m["claim"], m["evidence"],
                                  m["created"]))
        self.con.commit()

    def propagate_revocations(self) -> List[str]:
        """Revoke facts whose external dependency (another project's fact) is revoked."""
        revoked = {(r["project"], r["id"]) for r in
                   self.con.execute("SELECT project, id FROM lib_facts WHERE status='revoked'")}
        done = []
        for r in self.con.execute("SELECT * FROM lib_facts WHERE status='active'").fetchall():
            for dep in json.loads(r["external_deps"] or "[]"):
                proj, _, fid = dep.partition("/")
                if any(p == proj and i.startswith(fid) for p, i in revoked):
                    Store(r["project"]).revoke(r["id"], f"external dependency {dep} was revoked")
                    done.append(f"{r['project']}/{r['id'][:12]}")
                    break
        if done:
            self.sync()
        return done

    # ------------------------------------------------------------ queries
    def facts(self, query: Optional[str] = None, kind: Optional[str] = None, limit: int = 200):
        sql, args = "SELECT * FROM lib_facts WHERE 1=1", []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        for w in (query or "").split():
            sql += " AND (title LIKE ? OR description LIKE ? OR project LIKE ?)"; args += [f"%{w}%"] * 3
        sql += " ORDER BY created DESC LIMIT ?"; args.append(limit)
        return self.con.execute(sql, args).fetchall()

    def notes(self, kinds=None, query: Optional[str] = None, limit: int = 100):
        kinds = list(kinds or NOTE_KINDS)
        sql = f"SELECT * FROM lib_notes WHERE kind IN ({','.join('?' * len(kinds))})"
        args = list(kinds)
        for w in (query or "").split():
            sql += " AND (claim LIKE ? OR evidence LIKE ?)"; args += [f"%{w}%"] * 2
        sql += " ORDER BY created DESC LIMIT ?"; args.append(limit)
        return self.con.execute(sql, args).fetchall()

    def external_fact(self, ref: str):
        """'<project>/<id prefix >= 8>' -> the active library row, or raise."""
        proj, _, fid = ref.partition("/")
        if not proj or len(fid) < 8:
            raise ValueError(f"external dependency {ref!r} must be '<project>/<fact id, >= 8 hex chars>'")
        rows = self.con.execute("SELECT * FROM lib_facts WHERE project=? AND substr(id,1,?)=?",
                                (proj, len(fid), fid)).fetchall()
        if len(rows) != 1:
            raise ValueError(f"external dependency {ref!r} does not identify exactly one fact")
        if rows[0]["status"] != "active":
            raise ValueError(f"external dependency {ref!r} is revoked")
        return rows[0]

    # ------------------------------------------------------------ topics (planner)
    def topics(self, portfolio: Optional[str] = None):
        if portfolio:
            return self.con.execute("SELECT * FROM topics WHERE portfolio=? ORDER BY created", (portfolio,)).fetchall()
        return self.con.execute("SELECT * FROM topics ORDER BY created").fetchall()

    def topic(self, name: str):
        return self.con.execute("SELECT * FROM topics WHERE name=?", (name,)).fetchone()

    def propose(self, portfolio: str, name: str, layer: str, title: str, task: str, rationale: str,
                workers: int, rounds: int) -> None:
        if self.topic(name) is not None or (self.root / name).exists():
            raise ValueError(f"topic/project name {name!r} already exists")
        if not name.replace("_", "").replace("-", "").isalnum() or len(name) > 60:
            raise ValueError("topic name: letters, digits, '_' or '-', at most 60 characters")
        if not 1 <= workers <= 16 or not 1 <= rounds <= 20:
            raise ValueError("workers must be 1..16 and rounds 1..20")
        self.con.execute("INSERT INTO topics(name,portfolio,layer,title,task,rationale,workers,rounds,created,updated)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (name, portfolio, layer, title, task, rationale, workers, rounds, time.time(), time.time()))
        self.con.commit()

    def set_topic(self, name: str, **kw) -> None:
        if self.topic(name) is None:
            raise ValueError(f"no topic {name!r}")
        kw["updated"] = time.time()
        sets = ",".join(f"{k}=?" for k in kw)
        self.con.execute(f"UPDATE topics SET {sets} WHERE name=?", (*kw.values(), name))
        self.con.commit()
