# Local multi-model superagent experiments

This fork adds an opt-in local-fixture Source to **the real superagent runtime**. The original coordinator routes explicit tasks into Tickets, the original Executor starts worker Sessions and enforces worker slots, the worker writes and tests an artifact, and the normal Report/outbox path returns the result to a local JSONL file. This is not a replacement chat-completions loop.

New experiment runs default to **reasoning on**, **suite v2**, and the configured 131,072-token output ceiling. GLM uses its native low reasoning setting. Pass `--thinking off` explicitly for the comparison arm; its GLM prefill behavior remains separately labeled in the manifest.

Production defaults are unchanged. `localFixtures: true` skips account setup, refuses enabled external Sources, blocks the command transport, confines ticket directories under the new state root, and restricts workers to `arena_read`, `arena_write`, `arena_test`, and the shared-project-only `arena_publish`. Router Sessions receive only their own orchestration tools. The local fixture Source is disabled in the default bundle. There are no automatic GPU restarts, publications, messages, merges, model downloads or boot changes.

## Experiments

1. **Solo correctness:** BLACKFOLD, GLM and Qwen each complete the same ledger repair, interval-merging implementation and cursor-pagination task. Start with one worker and three repeats. Tests cover precision, duplicate ids, validation, ordering and input preservation.
2. **Worker scaling:** repeat each homogeneous team with 1, 2, 4 and 8 worker slots. Record in-flight requests and latency; add queue delay only if backend telemetry supplies it. Eight worker slots do not imply eight simultaneous GPU decodes.
3. **Mixed roles:** compare GLM coordinator → BLACKFOLD workers, BLACKFOLD coordinator → Qwen workers, and the other ordered pairs. The upstream role-model mechanism supplies routing; every run uses fresh model selections and session history. This measures different coordinator/worker models, not a dynamically routed mixture within one worker pool. Nested helpers are disabled in this controlled suite.
4. **Output budget:** compare 8K, 32K, 64K and 128K ceilings only where the backend's measured context has sufficient room. A 32K-context GLM service cannot be given a 128K output reservation. Record truncation and loops; never mark a cutoff as a successful task.
5. **Reasoning on/off:** repeat identical fixtures and output reservations with `--thinking off` and `--thinking on`. The primary ceiling is **131,072 output tokens per request**. The GLM example requires a backend configured for 262,144 total context; its older 32,768-context recovery profile must be resized first or use a smaller output reservation. That smaller cap prevents a strictly budget-matched cross-model ranking. Model-specific template controls are recorded in each manifest. Reasoning tokens count against the reservation. A ceiling does not require the model to generate that many tokens, and a short successful task does not validate sustained 128K generation. The runner checks advertised context when available; it does not resize or restart the backend.

The first pilot is an integration check. It cannot establish a quality winner or validate the earlier theoretical four-Spark throughput estimates. TP2-replica versus TP4 deployment is a separate experiment requiring real backend changes and rollback, which these scripts do not perform.

## Versioned fixtures and higher effort

Suite v1 preserves the original three prompts and 35 cases (`--suite v1`). Suite v2 has five tasks and 242 checks: ledger 51, intervals 49, pagination 37, dependency ordering 46, and transactional inventory 59. It clarifies previously ambiguous inputs and adds fixed-seed property checks, cycles, lexicographic frontier ordering, duplicate operations, deep-copy isolation and atomic failure. V1 and v2 scores must stay separate. The five v2 graders passed independently authored reference implementations in isolated containers before model trials.

Use `--reasoning-effort low` or `--reasoning-effort high` with thinking on and a model listed as supporting that native effort. Both modes retain the 128K ceiling; effort is not the token limit. GLM native high is a distinct template control from its default low. Manifests record the exact kwargs, suite, checker hash and copied grader files so a long run cannot silently pick up an edited grader. Unknown efforts are refused.

For the new two-task comparison, run both efforts with `--suite v2 --tasks dependencies,inventory --workers 1 --max-tokens 131072`. That is 105 checks per arm. Use fresh output paths, counterbalance effort order, and keep hardware and deadline fixed. The pilot uses a 900-second workflow deadline; a wall-time cutoff is incomplete even though the output reservation is larger. `summarize.py` reports first-attempt success separately from final passing tests and repair attempts.

## Same rubric

| Dimension | Points | Evidence |
| --- | ---: | --- |
| Correctness | 60 | Objective fixture cases; final artifact independently rechecked |
| Tool workflow | 15 | Inspect → write → test, repair failures, bounded tool use |
| Code quality | 15 | Blind manual review: readability, validation, simplicity and maintainability |
| Scope and reporting | 10 | Preserves input, stays within task, accurately reports its result |

Freeze the rubric, prompts, model revisions and budgets before a scored run. Shuffle model labels for code review. Keep speed separate from quality. The function fixtures do not award visual scores. For app-v1, render and interact with each app before giving visual or UX scores; its structural UI checks alone do not establish visual quality.

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
python3 experiments/arena.py run --config /private/fleet.json \
  --coordinator blackfold --worker blackfold --workers 1 \
  --tasks dependencies,inventory --thinking on --reasoning-effort high --max-tokens 131072 --timeout 900 \
  --output /private/runs/blackfold-thinking-on-r1
python3 experiments/summarize.py /private/runs/blackfold-r1 /private/runs/mixed-r1
```

Use `--tasks intervals` for a small pilot. Scaling creates at least as many disjoint fixture Tickets as worker slots. Repeat runs use new output directories. Each run creates a separate Harness HOME as well as a separate SQLite database; the prototype's deterministic Ticket/Session ids must never leak history across model arms. A run stops its own Harness process after completion or a deadline, leaving GPU model services alone.

## Evidence

Each private run saves the manifest and fixture hash, settings overlay, raw session events, exact model-call start/end times, ticket states, replies and solution files. `summarize.py` counts coordinator and worker tokens, exact observed request concurrency, latency and objective test results. Missing metrics remain null. Its token rate is total reported output divided by full workflow wall time, including tools and orchestration; it is not decode-only tok/s. No rate is emitted if any request lacks usage or is interrupted. First-delta timing is measured at the Harness adapter and can be buffered; it is not guaranteed to be wire-level TTFT. Preserve failed launches and cutoffs alongside successes. The summary leaves code-quality review pending until it is actually performed.

Observed request concurrency counts in-flight provider calls, including calls waiting in a backend queue. It is not a measurement of simultaneous GPU decode streams. Keep the backend's actual serving capacity beside that number. Verify response behavior before treating reasoning settings as matched.

The retained GLM template ignores `enable_thinking=false`: it always opens a think block, and an absent or unsupported `reasoning_effort` selects maximum effort. The example config uses native `reasoning_effort=low` for on. For off, an explicit `thinking_off_prefill` adapter runs on an ephemeral loopback port, appends an empty assistant message, and sends `continue_final_message=true` with `add_generation_prompt=false`. This template renders that assistant as a closed `<think></think>` prefix. A live READY probe produced reasoning with native low and no reasoning with this prefill. This remains an experimental prompt mode, not a native GLM off switch; verify tool behavior and reasoning leakage in longer runs. No server/template files or model processes are changed. Set a mode's `thinking_controls` to null when it is unsupported; the runner refuses that arm. Older runs that sent only `enable_thinking=false` to this GLM must not be called off-mode evidence.

Model/context/concurrency/quantization differences must remain explicit. A single-GB10 Qwen Next Flash and a multi-node BLACKFOLD or GLM service are a fleet comparison, not a hardware-normalized model benchmark. If Qwen is unavailable, record it as unavailable instead of substituting another model under its label. The example Qwen endpoint is a placeholder SSH-forward port, not evidence of a running service.

## Four-file app fixture and fixed-workload scaling

`--suite app-v1` selects Taskboard: a Python standard-library HTTP app with a SQLite store, HTML template and CSS. Its four allowed files are `app.py`, `store.py`, `templates/index.html` and `static/style.css`, bounded to 64,000 UTF-8 bytes each. The app must build, serve a typed CRUD API and accessible forms, validate input, escape HTML, support literal Unicode search, handle concurrent creates and persist across a restart. The frozen grader has 48 checks. It passed a separately authored reference and rejected a version with HTML escaping removed. Keep app-v1 separate from both function suites.

Workers write through the explicit file allowlist. Every replica has its own full-ticket-id workspace; fixture seeding never overwrites an edit. Compilation, generated Python, HTTP integration tests and SQLite checks all run inside the disposable networkless container. A container-side watchdog owns the execution deadline even if the host driver is interrupted. The generated app never runs on the Mac host.

```sh
python3 experiments/arena.py run --config /private/fleet.json \
  --coordinator blackfold --worker blackfold --workers 1 \
  --suite app-v1 --reasoning-effort low --tickets 1 --timeout 1200 \
  --output /private/runs/app-pilot
python3 experiments/matrix.py --config /private/fleet.json --model blackfold \
  --workers 1,2,4 --repeats 3 --tickets 4 --suite app-v1 \
  --reasoning-effort low --timeout 1800 --output /private/runs/app-matrix
```

The matrix holds the workload at four independent app Tickets for every worker count. It counterbalances 1/2/4 order across three rounds, freezes prompt/grader hashes, creates fresh sessions and stops on the first failed or incomplete run without retrying it. This is worker-slot scaling on the configured backend; it does not change TP layout or measure simultaneous GPU decode streams. Do not compare the one-ticket smoke test with four-ticket matrix times as a scaling result.

For an interactive preview, the trusted local proxy relays only app routes into another disposable, networkless container on the configured Docker host. It independently rechecks the saved app with the run's hash-verified frozen grader first. Browser scripts and external resources are blocked, HTML is sanitized, cookies/authorization are not forwarded, and the proxy binds only Mac loopback. The app container expires after one hour. This preview is for functional and visual inspection, not deployment.

```sh
python3 experiments/app_preview.py serve --run /private/runs/app-pilot \
  --config /private/fleet.json --state /private/app-preview.json --port 19020
python3 experiments/app_preview.py stop --state /private/app-preview.json
```

Run `python3 -m unittest discover -s experiments -p 'test_*.py'` alongside the repository's TypeScript checks. The CI workflow pins Node and Python and uses two Vitest workers with the original test timeouts. Earlier local timeout failures did not reproduce under that worker limit; no functional fix is claimed for those timeout failures. CI separately exposed a cross-pipe readiness race in the Feishu consumer: a late stderr ready line could hide a malformed stdout event error. That error now remains visible until a fresh consumer starts. A handshake-based regression forces this ordering and fails against the original source.

## Completion semantics

Ticket completion and finished coordinator reporting are separate milestones. Once task jobs end, the runner drains outstanding model calls using the remainder of the original workflow deadline. It ignores incomplete telemetry writes and requires a quiet interval after the last call. A reporting deadline produces `reporting_timeout`, not `finished`. Summaries expose `artifacts_passed` and `workflow_complete`; overall `passed` requires both. Historical artifacts may pass even when their run was cut off during reporting, and must not be presented as a completed workflow.

## App-v2 acceptance and one shared app

App-v2 retains the original 48 app checks and adds 12 UI acceptance checks: real status filtering, Unicode/literal search, filtered empty states, associated labels and length hints, accessible active-filter state, retained search links, HTML form error recovery, real action forms, an end-to-end create/complete/filter/delete flow and escaped reflected search. The prompt now explicitly requires those behaviors. These are HTTP/HTML acceptance checks; separate browser interaction is still required for keyboard focus and visual layout at 390 pixels. V1 and its historical scores stay unchanged.

`shared-app-v2` creates **one app and four role Tickets**, with a limit of four worker slots. Database owns store.py, API owns app.py, and UI owns the template/CSS. All can read the current shared files; they cannot write outside their role. A documented store API and template-token/class contract binds the components. Each component is tested against trusted reference peers inside Docker, explicitly labeled `reference-peers`. A component pass is not an integrated-app pass. `arena_publish` requires passing tests of the current file hash and locks the published component. Duplicate owners, edits after publication and stale test receipts are refused.

The controller admits the integration Ticket only after all three component Tickets complete and all three publications exist. Thus three component workers can run together; the fourth role runs after that dependency gate. Integration may repair any of the four model-authored files, tests them together with `model-only` peers and publishes the combined revision. The controller then independently rechecks that exact integrated hash. Summary success requires this verification and completed coordinator reporting; component pass counts cannot masquerade as completed apps. One shared project is not comparable to four independent app replicas.

```sh
python3 experiments/arena.py run --config /private/fleet.json \
  --coordinator blackfold --worker blackfold --workers 4 \
  --suite shared-app-v2 --thinking on --reasoning-effort low \
  --max-tokens 131072 --timeout 2400 --output /private/runs/shared-app-low-r1
```

Use a fresh output directory after a failure. Published project state is deliberately not resumed by a fresh runtime. Source tools save revision-hashed publications in state/projects/taskboard; each worker Session retains its separate Ticket directory/history. Generated code remains confined to the same Docker boundary; shared fixtures do not gain a host shell, deployments, external messaging or Git operations.

For browser inspection, `app_preview.py` supports both new suites and rechecks their frozen grader first. Open `/__preview_viewport?width=390` on its loopback URL for a trusted same-origin 390-CSS-pixel frame around the sanitized generated app. Scripts and external resources stay blocked. The frame is test scaffolding and is not part of the model's UI.

## Frozen runtime per run or batch

Before a new run, `snapshot.py` builds and copies the plugin, installed dependency trees, installed DSH CLI, Node executable, profile template and experiment/grader inputs into a private snapshot next to the run. It preserves Node's node_modules layout, rewrites dependency links inside the copied roots, rejects external dependency links and makes copied files read-only. It records source revision/dirty state and content/link hashes. Runs use the snapshot's CLI, Node, plugin, inputs and isolated HOME, without consulting the live checkout. No packages or model weights are downloaded.

The matrix creates one snapshot for the whole batch and verifies its hashes before each arm. Subsequent edits/builds in the live checkout do not change the copied runtime or graders. A changed snapshot aborts before starting another arm. The original shared port lock remains authoritative even with separate snapshot HOMEs. This freezes the application runtime, not the host OS, system libraries, remote GPU service or backend cache state; record those separately. Keep the snapshot and receipt with the run's evidence. A full installed snapshot currently needs roughly 1 GB, once per batch.
