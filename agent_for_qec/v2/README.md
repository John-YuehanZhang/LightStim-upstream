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
| P1 | code: stabilizers commute, k and n as claimed, exact code distance = claimed d, computed from the stabilizers alone (logicals derived from the normalizer) | GF(2) algebra; MILP (HiGHS), z3 as fallback |
| P2 | declared blocks each hold exactly n data qubits; noiseless circuit fires no detector and flips no observable; the detector error model of the re-noised circuit is deterministic | stim sampling, DEM construction |
| P3 | every declared flow holds with sign on the logical segment the gate derives (block preparation and final readout removed); every Pauli is a logical of the code on the blocks; inputs and outputs generate all 2k logicals per block (a logical→record flow for measurements) | `stim.Circuit.has_flow` (unsigned result reported to diagnose sign errors) |
| P4 | exact circuit-level distance of the circuit re-noised by the gate (`lib/circuitops.standard_noise`), over all observables, = claimed value; fault-tolerant at this instance iff it equals d | detector-subset relaxations give a lower bound, lifted witness an upper bound, MILP (HiGHS) closes any gap; otherwise only bounds are reported |

What it proves: the stated properties of the concrete circuits submitted
(specific d), not of a code family. Trust base: stim, the GF(2) routines, z3 and
HiGHS (a SAT/LRAT certificate for the final rows is planned).

**Non-programmatic (recorded, never decides acceptance).**

| check | who | output |
|---|---|---|
| does the fact deliver what the task asks, do the flows describe the claimed operation, are comparisons and assumptions sound (whatever a program cannot decide) | refuters: independent processes the main agent sets on a fact (how many, what to attack) | `refute_status`; a refuted fact stays in the store and is listed separately until the human decides (`qec.py adjudicate`) |
| platform suitability (superconducting 2D, neutral atoms, trapped ions) | gate statistics; analysis after construction | ledger column |
| novelty | novelty role (web search after the result exists) + human sign-off | `novelty_status`: prior_found / no_prior_found / human_confirmed_new |

## Roles and processes

| role | runs | may | may not |
|---|---|---|---|
| main | once per round (+ closing pass) | read everything; publish route registry and guidance; assign workers; open challenges (number of refuters, focus); declare done | submit candidates; use the web |
| worker | in parallel, one per assignment | build and test; submit to the gate; write memory | use the web; edit the gate or LightStim |
| refuter | with the workers, one per challenge slot | attack one fact; record refuted (with a reproducible script) / doubtful / no_problem_found | read the workers' shared memory; use the web; revoke |
| novelty | at the end of the run | search the web; record novelty reports on facts that are not refuted | mark anything "new" |
| human | any time | init projects, adjudicate refutations, sign facts as new, set run status | |

Isolation (enforced, not requested in prompts):

- Every agent process runs in a bubblewrap sandbox (`lib/sandbox.wrap_agent`)
  that contains only system directories, the python environment, the harness
  binary, the repository read-only (without `.git`, v1 material, tasks, tests,
  runner, config and other projects' results), the project's own results
  read-only, its working directory and a fresh home directory. The store,
  credentials and the rest of the host do not exist inside. The credential is
  passed through the process environment, never on a command line.
- The store is reached only through `qec.py`, which talks over a unix socket
  to the service in the orchestrator (`lib/service.py`); role and worker name
  are bound to the socket, and file arguments must lie in the agent's working
  directory.
- Built-in tools per role via `--tools` (Bash, Read, Write, Edit, Glob, Grep;
  WebSearch/WebFetch only for novelty), `--permission-mode dontAsk`, no MCP,
  settings only from the run's own config directory. Bash keeps network access
  (the harness needs it); common web clients are denied for solving roles, and
  the novelty audit after acceptance is the real check.
- `build.py` runs in a second, smaller sandbox without network
  (`lib/sandbox.wrap_minimal`).
- Each agent process is pinned with `taskset` to its own block of cores.

Each process reads its credential once at start, so accounts are switched only
between processes.

## Shared store (`lib/store.py`, SQLite under `$QEC_RUNTIME_ROOT/<project>/`)

- `facts`: content-addressed (hash of the whole submission directory),
  written only by the gate, revocable with cascade to dependants.
- `memory`: typed entries (finding, example, counterexample, dead_end, obstacle,
  direction, plan, verification, review, novelty, guidance, route_registry,
  elaboration, lesson). Awareness, never a correctness source. Every gate
  outcome is logged as a `verification` entry including the lightest
  undetected logical error.
- `submissions`, `assignments`, `runs` (prompt SHA, repo HEAD, account, model,
  cost, turns for every process).

Accepted submissions are archived with the gate verdict in
`results/<project>/facts/<id>/{bundle/,verdict.json}`; the ledger
`results/<project>/LEDGER.md` is rendered from the store (`qec.py render`).

## Prompts (for the paper)

All prompts are English and version-controlled in `prompts/`:
`main.md`, `worker.md`, `refuter.md`, `novelty.md`, `shared/submission_format.md`,
and the domain files `domain/angles.md` and `domain/pitfalls.md` (placeholders
to be rewritten by the operator). The exact text sent to every process is
archived as `$QEC_RUNTIME_ROOT/<project>/prompts_used/<sha>_<role>.md` and its
SHA is recorded in the `runs` table.

## Models

`config/models.toml` assigns a provider and model to each role. Providers:
`claude_oauth` (pool of subscription tokens, probed and leased per process),
`anthropic_api`, `deepseek` (Anthropic-compatible endpoint, runs in Claude
Code), `openai` (needs the Codex CLI harness; not implemented yet).

## Operator policy (never in prompts; given to agents as `operating_rules` memory)

Resource and scheduling rules are operator knowledge, kept in code and
`config/models.toml`, never in the published prompts:

- `[orchestrator] max_workers`: workers per round = min(max_workers, accounts
  with quota for the worker model). The names w1..wN are stored as
  `workers_this_round`; the main agent sees only the names.
- Quota: `lib/quota.py` reads each account's 5-hour and 7-day windows (Haiku
  probe); models with their own limit are probed separately. A process cut off
  by a limit blocks that (account, model) until the reported reset time and is
  relaunched on another account; its assignment stays open. With no quota
  anywhere the loop sleeps `--wait-hours` (default 2) and re-reads.
- `max_relaunch`, `main_attempts`, CPU pinning (`cpus_per_agent`, `cpu_first`,
  `cpu_slots`).
- Parallelism changes speed, not results.
- The orchestrator writes these conditions into the store as `operating_rules`
  memory (round-wide: workers, cores, tool-call limit, no internet while
  solving; per process: number of subagents from its account's quota). Agents
  see them at the top of `qec.py status`; the prompts only say to follow them.
- Network: every agent process runs behind a recording proxy (`lib/netlog.py`);
  `runner/audit_network.py` lists non-harness destinations and network code in
  transcripts (`results/<project>/NETWORK_AUDIT.md`), run automatically at the
  end of each orchestrator run.

## Running

```
PY=/home/yuehan/miniconda3/envs/light_stim/bin/python
cd /nvme2n1/yuehan_zhang/LightStim-upstream
$PY agent_for_qec/v2/runner/quota_report.py --force                  # quota of every account
$PY agent_for_qec/v2/qec.py --project P init --task agent_for_qec/v2/tasks/calib_rotated/TASK.md --origin calibration
nohup $PY agent_for_qec/v2/runner/orchestrate.py --project P --rounds 3 \
      > /nvme2n1/yuehan_zhang/agent_for_qec_runtime/P.orchestrate.log 2>&1 &
$PY agent_for_qec/v2/qec.py --project P status      # inspect at any time
$PY agent_for_qec/v2/runner/launch.py --project P --role worker --worker w1 --dry-run   # show the sandboxed command
```

Tests (gate fixtures, exploits, store, roles, socket service, launcher,
agent sandbox, quota bookkeeping; no model calls):
`QEC_TEST_TMP=<scratch> PYTHONPATH=. $PY agent_for_qec/v2/tests/run_tests.py`.

## Run archive

`$QEC_RUNTIME_ROOT/RUNS.md` indexes every project (version, commit, status,
note); each project has `RUN_INFO.md` (written at init and at the end of each
run; `qec.py runinfo --status valid|historical|void --note ...`). Only runs
marked valid may be used in the paper. Read RUNS.md before any result.

## Version history

- v2.0 (2393019, 2085fce): orchestrated main/worker/reviewer/novelty, gate, store.
- v2.1 (d2c5d76, fcd012d): gate hardened after review (gate-derived noise and
  segment, blocks, sandboxed build), agent processes in bubblewrap, socket
  service, quota-aware waves, resource budget removed from prompts.
- v2.2: operating rules as agent memory; subagents restored under those rules;
  pitfalls reduced to check items (no results); refuters replace the reviewer,
  refuted facts listed separately for human decision; gate check P3b (blocks
  back on the same qubits in the same code); `resource_gate` kind for given
  magic states; recording proxy and network audit; run archive; ledger by
  section with layer and assumed resources.
