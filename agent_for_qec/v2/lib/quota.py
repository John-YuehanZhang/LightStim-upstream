"""Quota monitor for Claude subscription accounts.

Claude Code's stream-json output contains `rate_limit_event` records with the
account's usage windows:
    unifiedWindows.five_hour.utilization / resetsAt
    unifiedWindows.seven_day.utilization / resetsAt
These windows are per account and shared by all models. Some models (Fable)
additionally have their own limit that does not appear in the windows; it is
detected by calling that model (a rejected call costs nothing).

snapshot(...) probes every account with the cheapest model, optionally probes
model availability, and caches the result in <runtime>/quota.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, Optional

CHEAP_MODEL = "haiku"


def _probe(token: str, model: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    rl, res = None, None
    with tempfile.TemporaryDirectory() as td:
        try:
            p = subprocess.run(["claude", "-p", "Reply with exactly OK", "--model", model, "--max-turns", "1",
                                "--output-format", "stream-json", "--verbose"], cwd=td, env=env,
                               capture_output=True, text=True, timeout=240)
            out = p.stdout
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": "probe timeout"}
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "rate_limit_event":
            rl = d.get("rate_limit_info")
        elif d.get("type") == "result":
            res = d
    w = (rl or {}).get("unifiedWindows", {})
    return {"ok": bool(res) and not res.get("is_error"), "msg": str((res or {}).get("result"))[:160],
            "five_hour": w.get("five_hour"), "seven_day": w.get("seven_day"),
            "cost_usd": (res or {}).get("total_cost_usd", 0)}


def headroom(entry: dict) -> float:
    """Fraction of the binding window still available (0..1)."""
    us = [entry.get(k, {}).get("utilization") for k in ("five_hour", "seven_day")]
    us = [u for u in us if u is not None]
    if not entry.get("ok") or not us:
        return 0.0
    return max(0.0, 1.0 - max(us))


def snapshot(tokens: Dict[str, str], runtime: Path, models: Iterable[str] = (), max_age_s: float = 1800,
             force: bool = False) -> dict:
    f = runtime / "quota.json"
    cached = json.loads(f.read_text()) if f.exists() else {}
    if not force and cached and time.time() - cached.get("ts", 0) < max_age_s \
            and all(m in cached.get("models_probed", []) for m in models):
        return cached
    snap = {"ts": time.time(), "models_probed": list(models), "accounts": {}}
    with ThreadPoolExecutor(max_workers=len(tokens)) as ex:
        base = dict(zip(tokens, ex.map(lambda a: _probe(tokens[a], CHEAP_MODEL), tokens)))
    for a, e in base.items():
        e["headroom"] = headroom(e)
        e["models"] = {}
        snap["accounts"][a] = e
    for m in models:
        live = [a for a, e in base.items() if e["headroom"] > 0]
        with ThreadPoolExecutor(max_workers=max(1, len(live))) as ex:
            res = dict(zip(live, ex.map(lambda a: _probe(tokens[a], m), live)))
        for a in tokens:
            r = res.get(a, {"ok": False, "msg": "account window exhausted"})
            snap["accounts"][a]["models"][m] = {"ok": r["ok"], "msg": r["msg"]}
    f.write_text(json.dumps(snap, indent=1))
    return snap


def model_ok(snap: dict, account: str, model: str) -> bool:
    e = snap["accounts"].get(account, {})
    if e.get("headroom", 0) <= 0:
        return False
    m = e.get("models", {}).get(model)
    return True if m is None else bool(m.get("ok"))   # model not probed separately -> window decides


def subagent_budget(h: float) -> int:
    """Parallelism does not change results, only speed: spend less of it when quota is low."""
    return 2 if h >= 0.5 else (1 if h >= 0.2 else 0)


def table(snap: dict) -> str:
    rows = []
    for a, e in sorted(snap["accounts"].items()):
        def fmt(k):
            w = e.get(k) or {}
            if "utilization" not in w:
                return "   -"
            return f"{round(100 * w['utilization']):3d}% (reset {time.strftime('%m-%d %H:%M', time.gmtime(w['resetsAt']))})"
        ms = " ".join(f"{m}={'ok' if v['ok'] else 'NO'}" for m, v in e.get("models", {}).items())
        rows.append(f"{a}  headroom={e.get('headroom', 0):.2f}  5h {fmt('five_hour')}  7d {fmt('seven_day')}  {ms}")
    return f"quota snapshot {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(snap['ts']))}\n" + "\n".join(rows)
