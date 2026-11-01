# Local multi-model superagent experiments

This fork adds an opt-in local-fixture Source to **the real superagent runtime**. The original coordinator routes explicit tasks into Tickets, the original Executor starts worker Sessions and enforces worker slots, the worker writes and tests an artifact, and the normal Report/outbox path returns the result to a local JSONL file. This is not a replacement chat-completions loop.

Production defaults are unchanged. `localFixtures: true` skips account setup, refuses enabled external Sources, blocks the command transport, confines ticket directories under the new state root, and restricts workers to `arena_read`, `arena_write`, and `arena_test`. Router Sessions receive only their own orchestration tools. The local fixture Source is disabled in the default bundle. There are no automatic GPU restarts, publications, messages, merges, model downloads or boot changes.

## Experiments

1. **Solo correctness:** BLACKFOLD, GLM and Qwen each complete the same ledger repair, interval-merging implementation and cursor-pagination task. Start with one worker and three repeats. Tests cover precision, duplicate ids, validation, ordering and input preservation.
2. **Worker scaling:** repeat each homogeneous team with 1, 2, 4 and 8 worker slots. Record in-flight requests and latency; add queue delay only if backend telemetry supplies it. Eight worker slots do not imply eight simultaneous GPU decodes.
3. **Mixed roles:** compare GLM coordinator → BLACKFOLD workers, BLACKFOLD coordinator → Qwen workers, and the other ordered pairs. The upstream role-model mechanism supplies routing; every run uses fresh model selections and session history. This measures different coordinator/worker models, not a dynamically routed mixture within one worker pool. Nested helpers are disabled in this controlled suite.
4. **Output budget:** compare 8K, 32K, 64K and 128K ceilings only where the backend's measured context has sufficient room. A 32K-context GLM service cannot be given a 128K output reservation. Record truncation and loops; never mark a cutoff as a successful task.

The first pilot is an integration check. It cannot establish a quality winner or validate the earlier theoretical four-Spark throughput estimates. TP2-replica versus TP4 deployment is a separate experiment requiring real backend changes and rollback, which these scripts do not perform.

## Same rubric

| Dimension | Points | Evidence |
| --- | ---: | --- |
| Correctness | 60 | Objective fixture cases; final artifact independently rechecked |
| Tool workflow | 15 | Inspect → write → test, repair failures, bounded tool use |
| Code quality | 15 | Blind manual review: readability, validation, simplicity and maintainability |
| Scope and reporting | 10 | Preserves input, stays within task, accurately reports its result |

Freeze the rubric, prompts, model revisions and budgets before a scored run. Shuffle model labels for code review. Keep speed separate from quality. If adding a UI app task later, render and interact with each app before giving visual or UX scores; this coding fixture suite does not award visual scores.

## Setup

Use the Node/DSH versions in `package.json` and `docs/006-deployment.md`, install dependencies, and run `pnpm check`. Create a **dedicated, previously unused** profile (the `--from-default-profile` command replaces an existing profile):

```sh
export DSH_HOME=/absolute/path/to/dedicated-experiment-home
dsh arena --from-default-profile web --dump-config > /tmp/arena-default.yml
dsh plugin --profile arena add "link:$PWD"
```

Copy `fleet.example.json` **outside this checkout** and fill in the real model endpoints, exact ids, context/output limits and hardware. Keep API credentials in environment references, never in Git. The loopback ports in the example can be backed by SSH forwards. `docker_ssh` is empty for local Docker or an existing SSH alias for a remote Docker host. Preload a Python 3.12 image and record its immutable image id in `python_image`; the runner never pulls an image.

Generated code executes only in a short-lived Docker container: no network, host mounts, GPUs or credentials; read-only root; unprivileged uid; no capabilities; bounded CPU, RAM and PIDs. Only the generated synthetic Python source and fixed test bootstrap enter it. A test exceeding its deadline fails and the exact owned container is removed. This is an experimental execution boundary, not a claim of protection against kernel vulnerabilities.

```sh
python3 experiments/arena.py preflight --config /private/fleet.json
python3 experiments/arena.py plan --config /private/fleet.json > /private/experiment-plan.json
python3 experiments/arena.py run --config /private/fleet.json \
  --coordinator blackfold --worker blackfold --workers 1 \
  --output /private/runs/blackfold-r1
python3 experiments/arena.py run --config /private/fleet.json \
  --coordinator glm --worker blackfold --workers 4 \
  --output /private/runs/mixed-r1
python3 experiments/summarize.py /private/runs/blackfold-r1 /private/runs/mixed-r1
```

Use `--tasks intervals` for a small pilot. Scaling creates at least as many disjoint fixture Tickets as worker slots. Repeat runs use new output directories. Each run creates a separate Harness HOME as well as a separate SQLite database; the prototype's deterministic Ticket/Session ids must never leak history across model arms. A run stops its own Harness process after completion or a deadline, leaving GPU model services alone.

## Evidence

Each private run saves the manifest and fixture hash, settings overlay, raw session events, exact model-call start/end times, ticket states, replies and solution files. `summarize.py` counts coordinator and worker tokens, exact observed request concurrency, latency and objective test results. Missing metrics remain null. Its token rate is total reported output divided by full workflow wall time, including tools and orchestration; it is not decode-only tok/s. No rate is emitted if any request lacks usage or is interrupted. First-delta timing is measured at the Harness adapter and can be buffered; it is not guaranteed to be wire-level TTFT. Preserve failed launches and cutoffs alongside successes. The summary leaves code-quality review pending until it is actually performed.

Observed request concurrency counts in-flight provider calls, including calls waiting in a backend queue. It is not a measurement of simultaneous GPU decode streams. Keep the backend's actual serving capacity beside that number. The profile explicitly requests thinking off through chat-template kwargs; verify the backend's response behavior before treating reasoning settings as matched.

Model/context/concurrency/quantization differences must remain explicit. A small Mac Qwen and a multi-node BLACKFOLD or GLM service are a fleet comparison, not a hardware-normalized model benchmark. If Qwen is unavailable, record it as unavailable instead of substituting another model under its label.
