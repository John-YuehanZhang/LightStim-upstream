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
    """Fraction of the binding window still available (0..1). An account that answered
    but reported no window data is given 0.5 (usable, unknown headroom)."""
    if not entry.get("ok"):
        return 0.0
    us = [(entry.get(k) or {}).get("utilization") for k in ("five_hour", "seven_day")]
    us = [u for u in us if isinstance(u, (int, float))]
    if not us:
        return 0.5
    return max(0.0, 1.0 - max(us))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def block_model(runtime: Path, account: str, model: str, until_ts: float, why: str) -> None:
    """Remember that `account` cannot serve `model` until `until_ts` (per-model limits
    are invisible in the shared windows)."""
    f = runtime / "model_blocks.json"
    b = _read_json(f)
    b.setdefault(account, {})[model] = {"until": until_ts, "why": why}
    _atomic_write(f, json.dumps(b, indent=1))


def model_blocked(runtime: Path, account: str, model: str) -> bool:
    e = _read_json(runtime / "model_blocks.json").get(account, {}).get(model)
    return bool(e) and time.time() < e.get("until", 0)


def snapshot(tokens: Dict[str, str], runtime: Path, models: Iterable[str] = (), max_age_s: float = 1800,
             force: bool = False) -> dict:
    f = runtime / "quota.json"
    cached = _read_json(f)
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
    _atomic_write(f, json.dumps(snap, indent=1))
    return snap


def model_ok(snap: dict, account: str, model: str, runtime: Optional[Path] = None) -> bool:
    e = snap["accounts"].get(account, {})
    if e.get("headroom", 0) <= 0:
        return False
    if runtime is not None and model_blocked(runtime, account, model):
        return False
    m = e.get("models", {}).get(model)
    return True if m is None else bool(m.get("ok"))   # model not probed separately -> window decides


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
