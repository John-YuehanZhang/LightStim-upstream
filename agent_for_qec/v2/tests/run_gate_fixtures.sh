#!/bin/bash
# Gate self-test: expects good_cnot_d3 accepted, bad_sign_d3 and bad_swapped_d3 rejected.
set -u
cd "$(dirname "$0")/../../.."
PY=${QEC_PYTHON:-/home/yuehan/miniconda3/envs/light_stim/bin/python}
export QEC_RUNTIME_ROOT=$(mktemp -d) QEC_RESULTS_ROOT=$(mktemp -d)
printf '# gate self-test\n' > $QEC_RUNTIME_ROOT/task.md
$PY agent_for_qec/v2/qec.py --project selftest init --task $QEC_RUNTIME_ROOT/task.md >/dev/null
fail=0
for pair in good_cnot_d3:accepted bad_sign_d3:rejected bad_swapped_d3:rejected; do
  f=${pair%%:*}; want=${pair##*:}
  got=$(QEC_ROLE=worker QEC_WORKER=selftest $PY agent_for_qec/v2/qec.py --project selftest submit agent_for_qec/v2/tests/fixtures/$f | python3 -c 'import sys,json,re; t=sys.stdin.read(); print(json.loads(t[:t.index("}")+1])["outcome"])')
  echo "$f: $got (expected $want)"; [ "$got" = "$want" ] || fail=1
done
rm -rf $QEC_RUNTIME_ROOT $QEC_RESULTS_ROOT
exit $fail
