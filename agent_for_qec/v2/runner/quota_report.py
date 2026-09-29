#!/usr/bin/env python3
"""Print the quota of every Claude account.  quota_report.py [--models claude-fable-5-1] [--force]"""
import argparse, sys
from pathlib import Path
V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "lib"))
from providers import load_config, _claude_tokens  # noqa: E402
from quota import snapshot, table  # noqa: E402
from store import RUNTIME_ROOT  # noqa: E402
ap = argparse.ArgumentParser(); ap.add_argument("--models", nargs="*", default=[]); ap.add_argument("--force", action="store_true")
a = ap.parse_args()
print(table(snapshot(_claude_tokens(load_config()["providers"]["claude_oauth"]), RUNTIME_ROOT, a.models, force=a.force)))
