"""Model providers for agent-for-QEC v2.

A provider turns (role config) into an environment for the harness process and
a credential lease. Only the Claude Code harness is implemented; providers that
speak the Anthropic Messages API (Claude subscription tokens, Anthropic API
keys, DeepSeek's Anthropic-compatible endpoint) all run through it.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

V2 = Path(__file__).resolve().parents[1]
FABLE_MODELS = {"claude-fable-5-1", "fable"}


def load_config(path: Optional[str] = None) -> dict:
    return tomllib.loads(Path(path or V2 / "config" / "models.toml").read_text())


def _secret_line(path: str) -> str:
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    raise RuntimeError(f"no secret in {path}")


@dataclass
class Lease:
    provider: str
    account: str
    env: Dict[str, str]
    harness: str
    model: str
    headroom: float = 1.0
    _fh: Optional[object] = field(default=None, repr=False)

    def release(self):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
            except Exception:
                pass
            self._fh = None


class NoCredential(RuntimeError):
    pass


# ------------------------------------------------------------------ claude subscription tokens
def _claude_tokens(cfg) -> Dict[str, str]:
    out = {}
    for line in Path(cfg["tokens_file"]).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            label, tok = line.split()
            out[label] = tok
    return out


def lease_claude_oauth(cfg: dict, model: str, runtime: Path) -> Lease:
    """Pick the free account with the most quota headroom for `model`.

    Headroom comes from the quota snapshot (5-hour and 7-day windows, shared by
    all models); models with their own limit (Fable) are probed separately.
    Accounts in reserve_for_fable are used for other models only as a last resort.
    """
    from quota import snapshot, model_ok
    tokens = _claude_tokens(cfg)
    separate = [model] if model in FABLE_MODELS else []
    snap = snapshot(tokens, runtime, models=separate, max_age_s=cfg.get("probe_cache_minutes", 30) * 60)
    reserve = set(cfg.get("reserve_for_fable", [])) if model not in FABLE_MODELS else set()
    lock_dir = runtime / "leases"
    lock_dir.mkdir(parents=True, exist_ok=True)
    live = [a for a in tokens if model_ok(snap, a, model, runtime)]
    live.sort(key=lambda a: (a in reserve, -snap["accounts"][a]["headroom"], a))
    for acct in live:
        fh = open(lock_dir / f"{acct}.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            continue  # another process holds this account
        return Lease("claude_oauth", acct, {"CLAUDE_CODE_OAUTH_TOKEN": tokens[acct]}, "claude", model,
                     snap["accounts"][acct]["headroom"], fh)
    raise NoCredential(f"no free Claude account with quota for {model}")


def free_accounts(cfg: dict, model: str, runtime: Path, force: bool = False) -> list:
    """Accounts that currently have quota for `model` (lock state not considered)."""
    from quota import snapshot, model_ok
    tokens = _claude_tokens(cfg)
    separate = [model] if model in FABLE_MODELS else []
    snap = snapshot(tokens, runtime, models=separate, max_age_s=cfg.get("probe_cache_minutes", 30) * 60, force=force)
    return [a for a in tokens if model_ok(snap, a, model, runtime)]


def unleased(runtime: Path, accounts: list) -> list:
    """Accounts not currently held by another process of this pipeline."""
    lock_dir = runtime / "leases"
    lock_dir.mkdir(parents=True, exist_ok=True)
    free = []
    for a in accounts:
        with open(lock_dir / f"{a}.lock", "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                free.append(a)
            except BlockingIOError:
                pass
    return free


def invalidate_quota(runtime: Path) -> None:
    try:
        (runtime / "quota.json").unlink()
    except FileNotFoundError:
        pass


def mark_exhausted(runtime: Path, account: str, model: str, resets_at: Optional[float] = None,
                   why: str = "hit limit during run") -> None:
    """Block (account, model) until the reported reset time (default: 2 hours) and drop
    the cached snapshot so the next selection re-reads the windows."""
    from quota import block_model
    block_model(runtime, account, model, resets_at or time.time() + 2 * 3600, why)
    invalidate_quota(runtime)


# ------------------------------------------------------------------ dispatcher
def acquire(role_cfg: dict, config: dict, runtime: Path) -> Lease:
    prov = role_cfg["provider"]
    pcfg = config["providers"][prov]
    model = role_cfg["model"]
    if prov == "claude_oauth":
        return lease_claude_oauth(pcfg, model, runtime)
    if prov == "anthropic_api":
        return Lease(prov, "anthropic_api", {"ANTHROPIC_API_KEY": _secret_line(pcfg["key_file"])}, "claude", model)
    if prov == "deepseek":
        key = _secret_line(pcfg["key_file"])
        env = {"ANTHROPIC_BASE_URL": pcfg["base_url"], "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_MODEL": model,
               "ANTHROPIC_SMALL_FAST_MODEL": pcfg.get("small_fast_model", model)}
        return Lease(prov, "deepseek", env, "claude", model)
    if prov == "openai":
        raise NotImplementedError("openai provider needs the Codex CLI harness; not implemented yet")
    raise ValueError(f"unknown provider {prov}")
