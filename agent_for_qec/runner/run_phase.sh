#!/bin/bash
# Run ONE agent-for-QEC phase as a headless Claude Code process.
#   run_phase.sh [MODEL=opus] [MAX_TURNS=250]
# Picks the account with quota for MODEL (least used first), launches
# `claude -p` with the fixed phase prompt in the repo on branch agent-for-QEC,
# logs stream-json to $STATE_DIR/logs/<ts>_<acct>.jsonl, appends usage accounting.
set -u
REPO=/nvme2n1/yuehan_zhang/LightStim-upstream
STATE_DIR=/nvme2n1/yuehan_zhang/agent_for_qec_runner
TOKENS=/nvme2n1/yuehan_zhang/.secrets/claude_oauth_tokens.txt
MODEL=${1:-opus}
MAX_TURNS=${2:-250}
mkdir -p "$STATE_DIR/logs"

cd "$REPO" || exit 2
if [ "$(git branch --show-current)" != "agent-for-QEC" ]; then
  echo "refusing to run: repo not on branch agent-for-QEC" >&2; exit 2
fi
if grep -q '^STATUS: DONE' agent_for_qec/PROGRESS.md; then echo "PROGRESS says DONE"; exit 0; fi

ACCT=$(python3 "$REPO/agent_for_qec/runner/select_account.py" --model "$MODEL") \
  || { echo "no account with quota for $MODEL" >&2; exit 3; }
TOK=$(awk -v a="$ACCT" '$1==a{print $2}' "$TOKENS")
TS=$(date +%Y%m%d_%H%M%S)
LOG="$STATE_DIR/logs/${TS}_${ACCT}_${MODEL}.jsonl"
ERR="$STATE_DIR/logs/${TS}_${ACCT}_${MODEL}.err"
echo "[$TS] phase start: account=$ACCT model=$MODEL max_turns=$MAX_TURNS log=$LOG"

# Permission model: no blanket bypass. Edits inside the repo are auto-accepted;
# shell use is limited to the python env, local git, and read-only inspection.
ALLOWED=(
  "Read" "Write" "Edit" "MultiEdit" "Glob" "Grep" "LS" "WebSearch" "WebFetch" "Task" "TodoWrite"
  "Bash(PYTHONPATH=:*)" "Bash(timeout:*)" "Bash(/home/yuehan/miniconda3/envs/light_stim/bin/python:*)"
  "Bash(git status:*)" "Bash(git log:*)" "Bash(git diff:*)" "Bash(git add:*)" "Bash(git commit:*)"
  "Bash(git show:*)" "Bash(git branch:*)" "Bash(git stash:*)" "Bash(git checkout -- :*)"
  "Bash(ls:*)" "Bash(cat:*)" "Bash(head:*)" "Bash(tail:*)" "Bash(wc:*)" "Bash(grep:*)" "Bash(find:*)"
  "Bash(mkdir:*)" "Bash(cp:*)" "Bash(mv:*)" "Bash(sed -n:*)" "Bash(diff:*)" "Bash(echo:*)" "Bash(date:*)"
)
export CLAUDE_CODE_OAUTH_TOKEN="$TOK"
env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p "$(cat "$REPO/agent_for_qec/prompts/phase_prompt.md")" \
    --model "$MODEL" \
    --permission-mode acceptEdits \
    --allowedTools "${ALLOWED[@]}" \
    --disallowedTools "Bash(git push:*)" "Bash(rm -rf:*)" "Bash(sudo:*)" "Bash(pip install:*)" \
    --max-turns "$MAX_TURNS" \
    --output-format stream-json --verbose \
    > "$LOG" 2> "$ERR"
RC=$?
unset CLAUDE_CODE_OAUTH_TOKEN

python3 - "$LOG" "$ACCT" "$MODEL" "$RC" "$STATE_DIR" <<'EOF'
import json, os, sys, time
log, acct, model, rc, sd = sys.argv[1:6]
res = None
for line in open(log, errors="ignore"):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("type") == "result":
        res = d
entry = {"ts": time.time(), "account": acct, "model": model, "exit": int(rc), "log": os.path.basename(log)}
if res:
    for k in ("total_cost_usd", "num_turns", "duration_ms", "terminal_reason", "is_error"):
        entry[k] = res.get(k)
    entry["cost_usd"] = res.get("total_cost_usd", 0)
    entry["result_head"] = str(res.get("result", ""))[:300]
    if res.get("is_error") and "limit" in str(res.get("result", "")).lower():
        sf = os.path.join(sd, "account_state.json")
        st = json.load(open(sf)) if os.path.exists(sf) else {}
        st.setdefault(acct, {})[model] = {"probed_at": time.time(), "available": False, "msg": "hit limit during run"}
        json.dump(st, open(sf, "w"), indent=2)
        entry["hit_limit"] = True
with open(os.path.join(sd, "usage_log.jsonl"), "a") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
print("[phase end]", json.dumps({k: v for k, v in entry.items() if k not in ("log", "ts")}, ensure_ascii=False))
EOF
exit $RC
