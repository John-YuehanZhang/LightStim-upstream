#!/usr/bin/env python3
"""Network audit of a project: did any solving process use the internet?

  audit_network.py --project P

1. Proxy logs (<runtime>/<project>/logs/*.net.log): every destination that is not a
   host the harness itself talks to (config [audit].harness_hosts) is listed.
2. Transcripts (<runtime>/<project>/logs/*.jsonl): every command and file an agent
   wrote is scanned for URLs and network code (urllib, requests, http.client,
   socket, curl, wget, ...). Hits are listed with their process and turn.
The novelty role is allowed to use the web and is reported separately.
Writes results/<project>/NETWORK_AUDIT.md; exit code 1 if a solving process has
a finding.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "lib"))
from store import Store  # noqa: E402
from providers import load_config  # noqa: E402

PATTERNS = [r"https?://", r"\burllib\b", r"\brequests\.", r"\bhttp\.client\b", r"\bsocket\.", r"\bhttpx\b",
            r"\baiohttp\b", r"\bcurl\b", r"\bwget\b", r"\bpip3? install\b", r"\bgit clone\b", r"\bconda install\b"]
RX = re.compile("|".join(PATTERNS))
DEFAULT_HOSTS = ["api.anthropic.com", "statsig.anthropic.com", "console.anthropic.com", "claude.ai",
                 "sentry.io", "api.deepseek.com", "storage.googleapis.com", "downloads.claude.ai"]


def _role(name: str) -> str:
    parts = Path(name).stem.split("_")
    return parts[3] if len(parts) > 3 else "?"


def _host_ok(host: str, ok: list) -> bool:
    return any(host == h or host.endswith("." + h) for h in ok)


def audit(project: str) -> tuple[Path, bool]:
    st = Store(project)
    cfg = load_config().get("audit", {})
    hosts = cfg.get("harness_hosts", DEFAULT_HOSTS)
    logs = st.dir / "logs"
    net_hits, text_hits, novelty = [], [], []
    for f in sorted(logs.glob("*.net.log")):
        role = _role(f.name.replace(".net", ""))
        for line in f.read_text().splitlines():
            d = json.loads(line)
            if not _host_ok(d["host"], hosts):
                (novelty if role == "novelty" else net_hits).append((f.name, d))
    for f in sorted(logs.glob("*.jsonl")):
        role = _role(f.name)
        turn = 0
        for line in f.read_text(errors="ignore").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "assistant":
                continue
            turn += 1
            for c in d.get("message", {}).get("content", []):
                if c.get("type") != "tool_use":
                    continue
                blob = json.dumps(c.get("input", {}))
                for m in RX.finditer(blob):
                    ctx = blob[max(0, m.start() - 80): m.end() + 80].replace("\\n", " ")
                    (novelty if role == "novelty" else text_hits).append((f.name, turn, c.get("name"), ctx))
    out = V2 / "results" / project / "NETWORK_AUDIT.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    L = [f"# Network audit — {project}", "",
         f"Harness hosts (expected): {', '.join(hosts)}", "",
         f"## Proxy: destinations outside the harness hosts, solving roles ({len(net_hits)})", ""]
    L += [f"- `{n}` {d['method']} {d['host']}:{d['port']} at {d['ts']:.0f}" for n, d in net_hits] or ["none"]
    L += ["", f"## Transcripts: network-looking code in solving roles ({len(text_hits)}; review by hand, "
              f"matches include harmless mentions)", ""]
    L += [f"- `{n}` turn {t} {tool}: `{ctx}`" for n, t, tool, ctx in text_hits[:500]] or ["none"]
    L += ["", f"## Novelty role (web use permitted): {len(novelty)} entries", ""]
    out.write_text("\n".join(L) + "\n")
    return out, bool(net_hits or text_hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    a = ap.parse_args()
    out, flagged = audit(a.project)
    print(f"{out}  ({'FINDINGS: review by hand' if flagged else 'clean'})")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
