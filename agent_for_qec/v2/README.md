# agent-for-QEC v2

An autonomous research system that searches for quantum error-correcting codes
and fault-tolerant implementations of logical operations, using LightStim as
its instrument. v2 replaces the single-process loop of v1 (`agent_for_qec/`
root) with an orchestrated design modelled on Danus (arXiv 2607.06447): a main
agent, a few parallel workers, a deterministic verification gate as the only
way to create facts, and a shared store that carries memory across processes.
Design notes and the survey behind it: `/nvme2n1/yuehan_zhang/agent_for_qec_survey/`.

## Verification: two classes

**Programmatic (decides acceptance; the analogue of a Lean kernel check).**
Implemented in `lib/gate.py`, run on a fresh rebuild of the submission:

| id | check | method |
|---|---|---|
| P1 | code: stabilizers commute, k and n as claimed, exact code distance = claimed d | GF(2) algebra; SAT (z3) closed from a witness |
| P2 | noiseless circuit fires no detector and flips no observable; the detector error model builds with no non-deterministic detector | stim sampling, DEM construction |
| P3 | every declared logical flow holds with sign | `stim.Circuit.has_flow` (unsigned result reported to diagnose sign errors) |
| P4 | exact circuit-level distance = claimed value; fault-tolerant at this instance iff it equals d | detector-subset relaxations give a lower bound, lifted witness an upper bound, MILP (HiGHS) closes any gap; otherwise only bounds are reported |

What it proves: the stated properties of the concrete circuits submitted
(specific d), not of a code family. Trust base: stim, the GF(2) routines, z3 and
HiGHS (a SAT/LRAT certificate for the final rows is planned).

**Non-programmatic (recorded, never decides acceptance).**

| check | who | output |
|---|---|---|
| specification faithfulness: the flows describe the intended gate, the noise model and rounds are adequate, the failure-mode list | reviewer role | `review_status` ok / flagged, text |
| platform suitability (superconducting 2D, neutral atoms, trapped ions) | gate statistics + reviewer paragraph | ledger column |
| novelty | novelty role (web search after the result exists) + human sign-off | `novelty_status`: prior_found / no_prior_found / human_confirmed_new |

## Roles and processes

| role | runs | may | may not |
|---|---|---|---|
| main | once per round | read everything; publish route registry and guidance; assign workers; declare done | submit candidates; use the web |
| worker | in parallel, one per assignment | build and test; submit to the gate; write memory | use the web; edit the gate or LightStim |
| reviewer | after workers, if facts await review | review, revoke | use the web |
| novelty | after review | search the web; record novelty reports | mark anything "new" |
| human | any time | init projects, sign facts as new, everything else | |

Permissions are enforced by the per-role tool allowlist in `runner/launch.py`
and by role checks in `qec.py`. Each process reads its credential once at
start, so accounts are switched only between processes.

## Shared store (`lib/store.py`, SQLite under `$QEC_RUNTIME_ROOT/<project>/`)

- `facts`: content-addressed (hash of build.py + submission.json + dependencies),
  written only by the gate, revocable with cascade to dependants.
- `memory`: typed entries (finding, example, counterexample, dead_end, obstacle,
  direction, plan, verification, review, novelty, guidance, route_registry,
  elaboration, lesson). Awareness, never a correctness source. Every gate
  outcome is logged as a `verification` entry including the lightest
  undetected logical error.
- `submissions`, `assignments`, `runs` (prompt SHA, repo HEAD, account, model,
  cost, turns for every process).

Accepted submissions are copied to `results/<project>/facts/<id>/`; the ledger
`results/<project>/LEDGER.md` is rendered from the store (`qec.py render`).

## Prompts (for the paper)

All prompts are English and version-controlled in `prompts/`:
`main.md`, `worker.md`, `reviewer.md`, `novelty.md`, `shared/submission_format.md`,
and the domain files `domain/angles.md` and `domain/pitfalls.md` (placeholders
to be rewritten by the operator). The exact text sent to every process is
archived as `$QEC_RUNTIME_ROOT/<project>/prompts_used/<sha>_<role>.md` and its
SHA is recorded in the `runs` table.

## Models

`config/models.toml` assigns a provider and model to each role. Providers:
`claude_oauth` (pool of subscription tokens, probed and leased per process),
`anthropic_api`, `deepseek` (Anthropic-compatible endpoint, runs in Claude
Code), `openai` (needs the Codex CLI harness; not implemented yet).

## Running

```
PY=/home/yuehan/miniconda3/envs/light_stim/bin/python
cd /nvme2n1/yuehan_zhang/LightStim-upstream
$PY agent_for_qec/v2/qec.py --project calib_rotated init --task agent_for_qec/v2/tasks/calib_rotated/TASK.md
nohup $PY agent_for_qec/v2/runner/orchestrate.py --project calib_rotated --rounds 3 --workers w1,w2 \
      > /nvme2n1/yuehan_zhang/agent_for_qec_runtime/calib_rotated.orchestrate.log 2>&1 &
$PY agent_for_qec/v2/qec.py --project calib_rotated status      # inspect at any time
```

Gate self-test (good / wrong-sign / over-claimed fixtures):
`agent_for_qec/v2/tests/fixtures/`, see `tests/run_gate_fixtures.sh`.
