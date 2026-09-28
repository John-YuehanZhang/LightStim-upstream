#!/usr/bin/env python3
"""Pick the Claude account for the next agent-for-QEC phase.

Usage: select_account.py [--model MODEL] [--force-probe]
Prints the chosen account label (never the token); exit 1 if none has quota.

Policy (user, 2026-09-27):
  * only accounts whose quota for MODEL is currently available are eligible;
  * prefer the account we have used least in the last 5 hours (our own
    accounting in usage_log.jsonl), then least in 7 days;
  * switching only happens between phases (this script runs before each
    `claude -p` launch), so a running phase is never interrupted.

Probe = one one-word `claude -p` call. An exhausted account answers
is_error=true / "reached your ... limit" at zero cost; a working account
answers "OK" (small cost), so probe results are cached PROBE_CACHE_MIN minutes.
"""
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path

STATE_DIR = Path("/nvme2n1/yuehan_zhang/agent_for_qec_runner")
TOKENS = Path("/nvme2n1/yuehan_zhang/.secrets/claude_oauth_tokens.txt")
PROBE_CACHE_MIN = 30
# Accounts that still have Fable quota are reserved for Fable phases: when
# running any other model they sort last (used only if nothing else has quota).
RESERVE_FOR_FABLE = {"acct2", "acct7"}
FABLE_MODELS = {"claude-fable-5-1", "fable"}


def load_tokens():
    out = {}
    for line in TOKENS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            label, tok = line.split()
            out[label] = tok
    return out


def probe(label, tok, model):
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = tok
    with tempfile.TemporaryDirectory() as td:
        try:
            r = subprocess.run(["claude", "-p", "Reply with exactly the word OK and nothing else.",
                                "--model", model, "--max-turns", "1", "--output-format", "json"],
                               cwd=td, env=env, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            return False, "probe timeout"
    try:
        d = json.loads(r.stdout)
    except Exception:
        return False, f"no json (exit {r.returncode}): {r.stderr[:200]}"
    if d.get("is_error"):
        return False, str(d.get("result"))[:120]
    return str(d.get("result", "")).strip().startswith("OK"), str(d.get("result"))[:40]


def usage_since(usage_file, label, seconds):
    now, tot = time.time(), 0.0
    if usage_file.exists():
        for line in usage_file.read_text().splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("account") == label and now - e.get("ts", 0) <= seconds:
                tot += float(e.get("cost_usd") or 0)
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opus")
    ap.add_argument("--force-probe", action="store_true")
    a = ap.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / "account_state.json"
    usage_file = STATE_DIR / "usage_log.jsonl"
    st = json.loads(state_file.read_text()) if state_file.exists() else {}
    tokens = load_tokens()
    now = time.time()
    eligible = []
    for label, tok in tokens.items():
        rec = st.get(label, {}).get(a.model, {})
        if (now - rec.get("probed_at", 0)) < PROBE_CACHE_MIN * 60 and not a.force_probe:
            avail = rec.get("available", False)
        else:
            avail, msg = probe(label, tok, a.model)
            st.setdefault(label, {})[a.model] = {"probed_at": now, "available": avail, "msg": msg}
            state_file.write_text(json.dumps(st, indent=2))
        if avail:
            eligible.append(label)
    if not eligible:
        print("NO_ACCOUNT_AVAILABLE", file=sys.stderr)
        sys.exit(1)
    reserve_penalty = (lambda l: l in RESERVE_FOR_FABLE) if a.model not in FABLE_MODELS else (lambda l: False)
    eligible.sort(key=lambda l: (reserve_penalty(l), usage_since(usage_file, l, 5 * 3600),
                                 usage_since(usage_file, l, 7 * 86400), l))
    print(eligible[0])


if __name__ == "__main__":
    main()
