---
name: agentour-compiler
description: Automatically create, reconstruct, validate, fidelity-test, and publish Agentour Agents. Trigger when a user wants to invent an Agent, convert or refactor an existing Agent project, package Agents for Agentour, or upload Agents. This is the single user-facing entry; it internally uses brainstorm, grill-me, and validation stages and strictly asks only one question or choice per conversational turn.
---

# Agentour Compiler

Own the complete workflow. The user must not orchestrate skills, commands, phases, validation, or retries.

## Non-bypassable bootstrap gate

Immediately after reading this Skill, before any user-facing explanation or workflow question, run:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" bootstrap
```

Do not say “I will use Agentour Compiler” first. Do not enter Brainstorm, inspect requirements, or ask
the Agent's purpose until the command returns `ready_for_interview: true`.

- `restart_required`: stop and ask for a new Thread.
- `platform_choice_required`: ask only the fixed platform choice, then rerun with
  `bootstrap --target-platform <test|production>`.
- `authorization_required`: tell the user that the Plugin opened the selected Agentour website and
  wait for browser authorization to complete. Never ask the user to copy a Token, browser Cookie,
  OIDC provider token, or any value from `localStorage`.
- `blocked`: stop and report the bootstrap error.
- `ready_for_interview`: use the returned Contract, recommended model, and active Compiler Tasks.

Bootstrap also returns `accepted_fix_tasks`. If the current user request addresses one of those tasks,
claim that exact task before editing and retain its `task_id`. Do not silently substitute an unrelated
feedback task for the user's request. When a requested Agent update matches accepted user feedback,
show the linkage briefly and treat the source feedback as an acceptance requirement.

The bootstrap transcript is the audit proof that update, identity, Contract, model probes and recovery
checks ran. Absence of this command means the workflow has not started correctly.

## Bootstrap internals: version check

The bootstrap command internally runs:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" check-update --auto
```

If it reports `updated: true`, stop this run and tell the user to start a new Codex Thread so the newly installed Plugin code is loaded. Do not continue using the old in-memory Skill. If the network check is temporarily unavailable, warn briefly and continue; if an update is known but automatic installation fails, stop and report the installer error.

## Absolute conversation rule

Every interactive turn may ask exactly one question or request exactly one choice.

- Never combine questions, even as bullets or numbered fields.
- Never ask the user to provide several examples or decisions at once.
- If a topic needs five facts, collect them over five rounds.
- A choice may contain mutually exclusive options, but it must resolve one decision only.
- Update working files after every answer, then ask the next single highest-value question.
- Continue all unblocked inspection, implementation, and validation between questions.

## Fixed platforms

| Choice | Name | URL |
|---|---|---|
| A | 测试服 | `https://test.agentour.ai` |
| B | 正式服 | `https://agentour.ai` |

Never ask the user to type a URL. 测试服 and 正式服 always use the fixed HTTPS URLs above.

## Mandatory dual state machine

Persist non-secret progress both in `.agentour/compiler-state.json` and the selected platform's
`/v1/dev/compiler-tasks` API. Never store the token. At startup, after authentication, list active
platform tasks and reconcile them with local state by task ID, Agent ID, operation, workspace ID,
Package hash, revision, and updated time. Platform job status wins over stale local `running` state.

- If local state exists, fetch its remote task and merge newer remote job results.
- If local state is missing, search active remote tasks for the same Agent/operation. One exact match
  resumes automatically; multiple plausible matches require one choice.
- Before any Package-changing stage transition, upload a clean Package checkpoint with
  `checkpoint-package`; a new workspace may restore it with `restore-checkpoint` and verify SHA-256.
- Continue existing Validation, Build, Eval, and Publish Job IDs instead of resubmitting them.
- When source, Manifest, model, or lockfile hashes change, invalidate from the earliest affected stage.
- Mark the platform task `completed` or `cancelled` at a terminal outcome.
- Record `stage_started_at`, `stage_finished_at`, and `duration_seconds` for discovery, conversion,
  environment preparation, local validation, platform validation, remote Build, Smoke/Evals, upload,
  and publish. Report the current stage by its real name; never call the entire Compiler run “上传”.

### 1. Platform choice

The first unresolved question must be exactly:

> 请选择发布平台：A. 测试服；B. 正式服。

Record the selected name and URL.

### 2. Browser authorization

First inspect the selected platform's saved OAuth credential:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/credential_store.py" status <test|production>
```

If no valid credential is stored, `bootstrap` starts a one-time `127.0.0.1` callback, creates a high-
entropy PKCE S256 verifier/challenge plus state and nonce, and opens the selected Core authorization
page. The user signs in through Logto and approves there. The Plugin validates issuer, audience,
subject, scope, expiry, state and nonce before continuing. Refresh Token rotation and replay handling
are automatic. The credential script selects Windows Credential Manager PasswordVault, macOS
Keychain, Linux Secret Service, or WSL bridging, separated by testing/production. The Windows store
supports large rotating OAuth bundles and never writes their plaintext to disk. If no operating-system
credential store is available, stop; never persist OAuth credentials in a plaintext fallback file.

Validate or reauthorize immediately:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> authorize
```

When the user asks which account is connected, run `account` and report only its non-secret identity
fields. When the user asks to switch accounts, run `switch-account` directly: it revokes the current
server-side OAuth token family, removes that environment's operating-system credential, and opens a
fresh browser authorization. Do not ask the user to run credential-store commands. Testing and
production credentials remain independent.

- Never print, pass as a command-line argument, persist in the project, commit, or include OAuth credentials in a report.
- If validation fails, revoke the unusable local device credential and restart browser authorization.
- Do not advance until `GET /v1/dev/me` succeeds.

### 3. Model discovery

After token validation, first fetch the platform contract, then models:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> contract
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> models
```

The `models` command probes every model returned by the selected platform, removes failed models from `data`, sorts usable models by platform quality rank, and returns `recommended_model`. Unless the user explicitly names a model, requests a cost ceiling, or says to prioritize economy, always use `recommended_model`: the Plugin must never silently downgrade Agent quality to save cost. Economic tradeoffs belong to the developer. Inspect `filtered_unavailable` only for diagnostics. Use the contract's Smoke schema, Node/Eve versions, canonical model IDs, ignore rules, package limit, pricing unit, and runtime semantics. Run `model-probe <model>` once more immediately before generation and never use a model that fails.

### 4. Intent and source choice

Ask exactly:

> 这次是：A. 更新已发布的 Agent；B. 重构已有项目；C. 从零创建 Agent？

If the user already clearly requested create, update, reconstruct, or continue, do not ask again.
Create the matching local and remote Compiler Task immediately.

### 5A. Update an owned Agent

Call `GET /v1/dev/packages`, then resolve the requested target with
`resolve-update-intent <target>` (`POST /v1/dev/packages/update-intents` with JSON body
`{"target":"<target>"}`); match only Packages owned by the
validated developer identity. Exact ID continues; an exact name may continue after showing its
summary; fuzzy or multiple matches require one choice. A missing match must ask whether the name is
wrong or the user intended a new Agent—never silently create. Download the active immutable baseline,
inspect the highest SemVer, verify the archive hash, and perform a three-way comparison when they
differ. Preserve unaffected behavior and create a new immutable version. Recheck model availability,
examples, approvals, deliverables, Knowledge Contract, Smoke, Evals, and fidelity instead of inheriting
old claims blindly.

### 5A.1 Managed Forge source gate

Read `guides/forge-workflow.md` before using managed Repository source. Use only the frozen Core
developer routes implemented by `agentour_api.py`: `repositories`, `repository-create`,
`repository-status`, `repository`, `agent-source-prepare`, `agent-source-push`, `git-clone`, `git-push`,
`change-set-create`, `pull-request-status`, `pull-request-merge`, `source-revision`,
`source-revision-status`, `source-build`, `source-build-status`, `source-eval`,
`source-eval-status`, and `agent-release`. Mutating commands send deterministic idempotency keys; Core
derives and validates all Commit/tree/Build/Eval/Review/Drive Snapshot/Tag/Release lineage.

Repository creation uses `/v1/forge/repositories`. `git-clone` and `git-push` obtain a one-time
short-lived credential from `/v1/forge/git-credentials` and pass it to Git only through a temporary
credential-free askpass helper plus process environment. The plaintext credential must never be printed,
recorded, added to a remote URL, written to checkpoint state, or reused. The credential exchange itself
uses a fresh Idempotency-Key because Core intentionally returns `409` rather than replaying plaintext.

After submitting Build or Eval, resume the same remote record with `source-build-status` or
`source-eval-status`; an interrupted read is not authorization to create a replacement Job.

After every remote stage, write `save-forge-checkpoint` with only Repository ID, full Commit SHA,
current remote Job ID, contract version, and stage. Restore it with `restore-forge-checkpoint`; when the
Commit changed, discard the old remote Job and resume from `commit_changed`. Never place a Core token,
Git credential, URL credential, secret, or Drive credential in checkpoint state.

After pushing an exact head, use `change-set-create` to create or recover its PR, then use
`pull-request-status` for current Provider PR and Review facts. The Plugin never submits a Review or
performs author self-review. `pull-request-merge` defaults `--required-approvals` to `0`, but Core/Forge
remain authoritative for branch policy and independent Review; a policy rejection stays
`review_pending`. Use the returned merge Commit for Source Revision creation. Do not invent an endpoint,
use a long-lived Forgejo PAT, treat the local directory as source authority, or describe an unpushed
Commit / missing Source Revision as published. After Repository creation, wait for `repository-status`
to report an active, converged projection before requesting Git access.

For both new Agents and “更新我的 xxx Agent”, run `agent-source-prepare` with the immutable Agent ID
and canonical name. Pass `--repository-id` whenever Core already binds the Agent to a Repository.
The two identifiers are deliberately different domains: `--agent-id` must always be copied byte-for-byte
from the Package manifest or an existing Core binding, while `--name` is the Repository canonical name
and may normalize characters such as `_` to `-`. Never derive, slugify, normalize, or repair an Agent ID
from a Repository name. If Core returns `AGENT_SOURCE_ID_MISMATCH`, resume with the returned
`bound_agent_id` and the same Repository ID after confirming that it matches the Package manifest;
never migrate the binding, create a replacement Repository, or retry the mismatched ID.
Baseline priority is strict: an explicit `--ref` (full Commit, immutable Tag, or Branch), then a valid
local `.agentour/source.json` Agent ID + Repository ID binding, then the latest remote default branch.
Never infer a Repository from the directory name. A dirty local workspace is preserved and requires
the explicit `--use-local` choice; it must never be reset. Fast-forward a clean workspace only when
the fetched Commit proves ancestry. If local and remote diverge, stop for merge, rebase, or a new
branch. A locally-ahead Commit remains local authority and must not be discarded.
Before any `repository-create`, `agent-source-push`, Change Set, Source Revision, or Release write,
the Plugin must verify that the Agent registry record, Repository binding, effective policy, required
capabilities, and selected production transport are present and compatible. Never call
`repository-create` directly for a new Agent while skipping `agent-source-prepare`; Prepare is the
Agent binding gate and must complete in a dedicated Git workspace. A workspace that is itself a
Package directory must fail locally before it can create nested Git metadata.

After Package validation, use `agent-source-push` with exactly one explicit workspace-relative Package
directory. It refuses pre-existing staged changes, snapshots only that Package, maps its bytes to the
managed Repository root, and verifies the Commit tree exactly matches the Package file list. Compiler
state, flight records, Specs, legacy `packages/<agent-id>` paths, and unrelated workspace files must
not enter the Commit. It pushes the exact Commit
through a fresh short-lived Core credential. Never stage the entire workspace implicitly. Core—not the
Plugin—creates immutable release Tags and Forge Releases after all source and Drive gates pass.

After the pushed Commit has a fixed Source Revision and the required Pull Request/Review facts, publish
only through the unified transaction:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  agent-release --agent-id <agent-id> --version <version> --source-ref <branch-or-ref> \
  --source-revision-id <source-revision-id> --source-commit-sha <full-commit-sha> \
  --pull-request-number <number> --required-approvals <count> \
  --release-notes <text> --visibility <private|public>
```

After a Pull Request is merged, the release source ref is the Repository canonical default branch
(`main`) and the source commit must be the returned merge commit; normalize this automatically and
include the normalized ref in the idempotency key. This single operation merges the reviewed change, freezes the Drive Collection snapshot, creates the
immutable Tag and Forge Release, and records the Core Agent Version. Retry the same inputs with the same
derived operation ID; never create a Tag, Forge Release, or platform version directly from the Plugin.
For a tenant credential, `public` means visible inside that tenant only. It never requests global
platform publication; only the tenant owner can submit that separate platform-level request.

### 5B. Existing Agent inventory

Inspect before asking about anything discoverable. Inventory entrypoints, Agents, prompts, skills, tools, MCP servers, sub-agents, workflows, routing, tests, examples, dependencies, environment variables, external services, files, attachments, approvals, artefacts, retries, and failures.

If multiple Agents exist, ask one scope choice:

> 检测到多个 Agent。你希望：A. 合成一个 Agent；B. 分别转换并上传全部 Agent；C. 只转换其中一部分？

- For C, the next turn asks only which Agents to include; multi-select is allowed because it resolves one scope decision.
- For A, preserve every source Agent's role, routing, workflow, tools, and boundaries in one Package.
- For B, create one Package and fidelity report per source Agent.

Generate `.agentour/conversion-inventory.json`, `.agentour/conversion-map.json`, and `.agentour/fidelity-report.json`. Mark every capability `preserved`, `adapted`, `reimplemented`, `degraded`, `unsupported`, or `removed` only with explicit authorization.

### 5C. New Agent discovery

Create `AGENT_SPEC.md` immediately. Begin with one open invitation:

> 请尽可能完整地讲讲你想做的 Agent。可以包括给谁用、解决什么问题、用户会提供什么、它要执行哪些步骤、需要连接哪些系统，以及最后交付什么；不完整也没关系，我会整理后只追问关键缺口。

Extract that answer into a field-level evidence map with values, confidence, and sources:
`user_explicit`, `source_discovered`, `platform_discovered`, `inferred`, `defaulted`, or `missing`.
Then internally apply `agentour-brainstorm` and `agentour-grill-me`, asking exactly one question per turn
only for unresolved high-impact gaps or conflicts. A mature first answer may require few or zero further
questions. Keep guided one-question interviewing for vague ideas. Safe low-risk defaults do not deserve
separate turns; approvals, side effects, truth sources, severe failure consequences, minimum input,
completion, and deliverable acceptance must be explicit when inference would be risky.

Do not implement until the spec can reproduce the intended workflow. Do not ask for a separate implementation confirmation when creation was already authorized.

### Mandatory interaction and approval policy choices

Before generating or modifying Package files, ask exactly one interaction choice unless the user has
already answered it explicitly:

> 这个 Agent 收到消息后应该：A. 自动理解合理缺省并直接执行到最终结果；B. 持续追问，直到所有必要信息清晰后再执行（默认）？

Record `interaction_policy.execution_mode` as `auto_execute` or `clarify_until_ready` in AGENT_SPEC,
Compiler Task, and manifest. Default to `clarify_until_ready`. For `auto_execute`, specify safe defaults,
ordinary ambiguities that must not trigger questions, and hard boundaries where the Agent must fail
honestly instead of inventing identities, resource IDs, amounts, or irreversible targets.

If the Agent has send/write/payment/delete/permission side effects, ask exactly one separate choice:

> 危险操作应该：A. 每次执行前审批（默认）；B. 按已确认的 Agent 规则直接执行，不逐次审批？

Record `interaction_policy.dangerous_action_approval` as `always` or `none`. Default to `always`.
When the user explicitly selects `none`, approval tools may be omitted and `approval_required=[]`, but
instructions, README, examples, and Smoke must define the automatic write scope, idempotency, partial
failure behavior, evidence, and non-automatable high-risk boundary. Recommend approval for payment,
deletion, and permission expansion, but honor an explicit no-approval choice. Never combine these two
policy questions in one turn.

### Mandatory reference-material gate

Before generating or modifying Package files, explicitly resolve whether the Agent depends on the
expert's own documents, datasets, examples, SOPs, historical cases, databases, websites, repositories,
or an existing MCP knowledge source. Never infer “no reference material” merely because the initial
idea dump did not mention it. Unless the user already supplied materials or explicitly declined, the
next highest-value single question must be:

> 这个 Agent 是否需要使用你自己的参考资料或数据？如果需要，请提供本地文件路径，或说明它们来自网页、仓库、数据库还是现有 MCP；如果不需要，直接回答“不需要”。

When local files are provided, upload them to the authenticated user's Drive and associate each File
with the current Agent Collection before Package generation:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  upload-references --agent-id <agent-id> --repository-id <repository-id> \
  <file> [<file> ...]
```

The Plugin first ensures Core's immutable Agent↔Repository↔Collection binding, then uploads through
Core's OAuth facade; only Core holds Drive service credentials. Drive deduplicates identical bytes only
inside the same owner security domain while keeping separate visible File metadata. Never copy private
source content or account-specific File IDs into the Package. Runtime receives only the Collection scope
injected by Core; the Agent must not choose arbitrary Collection or File IDs.
For changing or live data, prefer a read-only connector/MCP source instead of taking a one-time file
snapshot. Record the user's decision and returned File IDs in compiler state so resumed runs do not ask
again or upload duplicates.

### Mandatory Feishu channel capability gate

When an Agent needs to read or change Feishu/Lark resources, read
`references/feishu-capabilities.md` completely before generating or modifying Package files.

Before touching Package files, run the strict official CLI preflight with the exact Skills the Agent
will need:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/lark_cli_preflight.py" \
  --skills lark-contact lark-task lark-base
```

This gate must confirm that the installed `lark-cli`, GitHub's latest `larksuite/cli` Release, and npm's
latest `@larksuite/cli` version are identical. It automatically invokes the official installer when the
CLI is absent or behind, verifies the bundled Skills, and reads each selected official Skill contract.
If GitHub or npm cannot be reached, their versions disagree, the upgrade does not converge, or a Skill
cannot be read, stop Feishu Agent generation. A cached version or remembered CLI syntax is not enough
to claim that development used the latest official contract.

- Treat Feishu as an Agentour-managed Channel Runtime capability. Never package OAuth tokens, the
  official CLI, official Skills, `FEISHU_APP_ID`, or `FEISHU_APP_SECRET`.
- Select the smallest official `lark-*` Skill set and declare it in
  `agentour.json.channel_capabilities.feishu.skills`. Set `required=true` when the Agent cannot fulfil
  its primary purpose without Feishu; otherwise use `false`.
- Instructions must load each declared Skill with Eve `load_skill` and follow that Skill's `lark-cli`
  contract. Do not guess CLI arguments, implement a private Feishu OpenAPI client, or ask any user to
  paste a token or application secret.
- The Package does not retrieve the long-lived credential. Agentour keeps the refresh token in its
  encrypted credential store and injects a short-lived user credential into the isolated Runtime only
  when the account has an active Feishu authorization and has granted this Agent access.
- The same account authorization may be used from authenticated Web and SDK entry points. A Feishu
  channel launch uses the sender's identity. Anonymous shares must never inherit the owner's Feishu
  credential.
- README must explain the prerequisite: authorize Feishu under Agentour's Channels page and enable this
  Agent. Missing authorization must produce an honest actionable failure, never a fabricated result.
- Smoke Tests verify Skill selection and failure handling without mutating the developer's real Feishu
  data; platform integration tests own OAuth, Scope, ACL, and real-resource coverage.

## Package generation

Create each Package under `packages/<agent-id>/` from bundled templates with `agentour.json`, `README.md`, `RELEASE.md`, `AGENT_WIKI.md`, `tests/smoke.yaml`, and a complete `payload/` Eve project.

Read `guides/agent-wiki.md` in full before creating, reconstructing, or updating Package files. Generate
`AGENT_WIKI.md` as the sanitized initial Wiki for the Agent: it must summarize the complete development
conversation flow, Agent objective, development-AI inputs and outputs, Runtime input/output contracts,
reference materials, interfaces/tools/external systems, workflow, approvals/security, validation, and
known limitations. It is Package content and may become public, so never paste a raw transcript,
credentials, private reference contents, personal data, local absolute paths, signed URLs, or unrelated
prompts. The terminal improvement-center flight recorder remains a separate required output.

For updates, read and preserve the existing Wiki history, refresh current-state sections, and append one
versioned change entry with objective, concrete changes, compatibility/migration notes, and verification.
If the baseline has no Wiki, create it and explicitly mark unavailable historical conversation facts
instead of inventing them. Update the Wiki before generating `package.lock`; if later validation or
release evidence changes it, regenerate the lock and rerun affected Gates.

Agentour currently supports E2B Runtime only. Do not create `payload/agent/sandbox.ts` or
`payload/agent/sandbox/sandbox.ts`. The platform injects its audited single-layer `agentour-e2b`
adapter into a disposable Package copy during Remote Build. A Package-authored Eve sandbox creates a
second execution boundary and must fail validation instead of being silently composed or overwritten.

Follow the fetched Compiler Contract literally. For Contract v4 and later: put behavioral instructions
in `payload/agent/instructions.md` (never `defineAgent.system`), do not throw for missing Runtime
credentials during module import/build, pin every direct dependency to an exact version, never use
`package.json#pnpm.overrides`, and copy the audited `templates/pnpm-workspace.yaml` so native Eve
dependencies use the remote Build's exact `allowBuilds` policy.

Preserve source business rules, orchestration, tool contracts, approvals, attachment behavior, output schemas, artefacts, retry behavior, and user-visible flow. Every capability needs business-readable `runtime_ui` labels. Never expose `load skill`, internal paths, or system prompts. `waiting_approval` means paused and waiting, never running.

Before generating files, turn the approved spec into an explicit acceptance contract. Every Package
must declare `deliverable.required=true`, at least one `deliverable.formats` value, and at least two
complete executable `examples`. Every generated tool must have a Chinese business name in
`tool_ux.<tool>.zh_name`. Every approval tool must provide `title`, `purpose`, `action`, `impact`,
`risk`, and `deny_effect` under `approval_ux`. Instructions must define minimum required input,
formal `ask_question` behavior, completion criteria, honest tool-failure reporting, at most one retry
for explicit transient failures, and a useful fallback deliverable. Do not leave template placeholders.

- Price in **积分** using `pricing.amount_credits`; never describe it as RMB cents.
- Use Smoke `schema_version: 1` and only `send`, `expect_tool`, `expect_contains`, `expect_approval`, and `expect_question`.
- With `clarify_until_ready`, missing required input must use Eve `ask_question`, producing `input_requested`.
- Every `clarify_until_ready` Agent must define its minimum required input, a remaining-gap list, and terminal completion
  conditions. After each answer, recompute all remaining gaps and ask exactly the next highest-priority
  missing item. While required input is missing, it must not emit a final deliverable or mark the run
  completed. Only successful completion, a non-recoverable failure with an actionable explanation, or
  explicit user cancellation may end the run. `input_requested` is neither success nor failure.
- Every `auto_execute` Agent must define safe defaults, avoid ordinary follow-up questions, disclose
  defaults in the final result, and fail honestly only when the minimum executable target cannot be
  resolved. It must not contain a hidden `ask_question` path that contradicts the selected policy.
- Check Node and pnpm before dependency work. Require Node 24; never compile Node from source.
- Generate the lockfile with `pnpm install --lockfile-only`. Do not install `node_modules` in the project merely to create the lock.
- If a local build is needed, use a Linux temporary copy or the platform-compatible container helper, then delete the temporary build directory.
- Maintain `.agentour/compiler-state.json` with contract version, publish jobs, failed Gates, repairs, and results; never include tokens.

### Mandatory runtime-efficiency contract

Runtime efficiency is part of correctness for every generated or updated Agent:

- Plan the complete turn once before repetitive execution; never ask the model to re-plan after every item.
- Load each Skill, schema, configuration, and resource structure at most once per Session and reuse it.
- Parallelize independent reads; batch or bounded-loop same-type writes according to the official contract.
- Prefer batch/bulk/upsert APIs. A list of N items must not cause approximately N model turns.
- Do not read after write when the write response already provides authoritative success evidence.
- Keep repeated stdout, Skill bodies, and per-item history out of the growing model context; maintain a
  compact structured progress ledger instead.
- Persist item-level external IDs and idempotency keys while executing so interruption recovery never
  restarts successful writes from zero.
- Instructions must state a reasonable model-planning-step target, tool-call order, context control,
  and recommended batch size. Smoke/Eval must include a multi-item case and record model steps, tool
  calls, input tokens, and elapsed time as a performance baseline.

Validation must reject an implementation that reloads the same Skill/schema per item, performs one
model turn per record, or grows full context linearly with every repeated operation even if the final
functional result is correct.

## Automatic validation and repair

Internally apply `agentour-validator` and run:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/validate_package.py" packages/<agent-id>
```

Static validation is not sufficient. Before asking visibility or publishing, every Package must pass both commands:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" build-test packages/<agent-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  validate-package packages/<agent-id>
```

`build-test` copies the Package to an isolated temporary directory, runs `pnpm install --frozen-lockfile` and `pnpm exec eve build`, then deletes the temporary dependencies. `validate-package` runs the platform's exact build and Smoke Gates without publishing or occupying a Registry version. Repair and repeat until both pass. Never use formal publish as the first real execution test.

Also generate the lockfile and run build, Smoke Tests, source tests, and relevant project tests. Fix failures narrowly and rerun until green or genuinely blocked. Never weaken valid tests.

## Fidelity for existing Agents

Build comparison cases from source tests, examples, sanitized cases, prompts, and workflows. Run the same cases against source and converted Agents when executable. Compare workflow and routing, tools and arguments, approvals, attachments, structured outputs, artefacts, normal/boundary/failure/retry/multi-turn behavior, semantic results, latency, and resources.

Bind the fidelity report to the Package SHA-256. A critical workflow, tool, approval, attachment, schema, or artefact mismatch blocks publishing regardless of total score. Repair and repeat until fidelity is as high as technically possible; disclose all remaining degradation.

## Visibility choice

After validation and fidelity work, ask exactly:

> 请选择上传方式：A. 私有；B. 公开（需要平台审核）。

For multiple Packages, first ask whether one setting applies to all or should be selected one by one. If one by one, ask one Package visibility per turn.

## Upload

Revalidate the token immediately before upload. Show one compact summary of platform, Agent IDs, versions, models, visibility, validation, fidelity, and limitations. If upload was already requested, proceed; otherwise ask one final upload confirmation.

Only after that explicit confirmation, run the paid-resource remote Build Gate. Never run it during discovery, interview, local validation, visibility selection, or while awaiting confirmation. Cached content does not consume a new E2B Build quota.

Immediately before consuming Build quota, run `build-preflight`. It must confirm the E2B service,
required Runtime Profile template, active-job capacity, hourly quota, daily quota, Node and Eve contract.
If it is not ready, preserve the task and Package checkpoint and wait; do not enter a doomed Build.

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <test|production> build-preflight
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <test|production> remote-build packages/<agent-id>
```

Use the structured `gates` result to repair deterministic failures. Do not retry unchanged content blindly; retry only after a repair or for the single transient retry handled by the platform. Publish only when the remote Build status is `succeeded`.

On HTTP `429`, explain that the active/daily E2B quota is exhausted and wait rather than
mutating content or looping retries. Cached Build results are valid and consume no new quota.
Cancel a superseded or user-cancelled job with `cancel-build <job-id>` and confirm its terminal
status before starting another paid Build.
If polling is interrupted or the local command times out, resume that exact paid Job with
`track-build <job-id>`; never resubmit merely because observation stopped.

Use only the `agent-release` command defined in §5A.1. Direct `publish`/`publish-async` package upload is
retired and must never be used as a publication fallback.

Follow every job. On Gate failure, fix, bump the version when required, rebuild fidelity evidence, and retry. Finish with final platform status and Package identifiers.

## Required terminal platform feedback

Maintain one continuous, redacted flight recorder throughout the run. Upload exactly one terminal
report for the run:

- If deployment eventually succeeds, upload the complete 18-section run flight recorder, including
  every temporary block, retry, recovery, and failed predecessor Job. Do not upload a separate
  blocker report for a condition that was later recovered.
- Only if the run is genuinely unable to reach deployment after the permitted repairs/retries, upload
  one detailed blocker report derived from the same recorded evidence. It must explain the last
  actionable state, exact redacted errors, attempts, Jobs, Package hashes, elapsed/stalled time, and
  why the Plugin can no longer make progress. Never replace it with a status-only summary.

Read `guides/feedback.md` in full and follow its evidence boundaries and required 18-section format.
The readable filename must be `<agent-readable-name>-<operation>-完整运行现象记录-<YYYYMMDD-HHmm>.md`.
Do not create a short/user/summary alternative. Persist evidence continuously through
`scripts/flight_recorder.py`; do not reconstruct failures, latency, Job transitions, Package hashes,
polling or unknown environment facts from memory after publishing.

Upload it with the same validated browser authorization to the selected platform:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <test|production> feedback "<readable-run-report>.md" \
  --plugin-version "<installed manifest version>" --operation <create|reconstruct|update> \
  --agent-id <agent-id> --publish-job <job-id>
```

For a blocked terminal run, use the best available Compiler Task, Validation, Build, or Publish Job ID
as `--publish-job`. Report the feedback ID to the user. Terminal feedback upload is required, not an
optional suggestion.

## Required optimization deposit for feedback-driven work

When this run claimed or was explicitly linked to an accepted Agentour feedback task, terminal flight
feedback is not enough. After implementation and verification, write a JSON result containing all of:

- `analysis`: the evidence-backed defect or optimization analysis;
- `summary`: the user-readable outcome;
- `changes`: concrete changed files/contracts/behaviors, not generic claims;
- `evidence`: tests, Jobs, before/after observations, and remaining limitations;
- `commits`: pushed commit SHAs when available.

Then complete the task through the platform API. This call automatically creates the durable
“优化沉淀” record visible beside platform and developer feedback:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  complete-fix <fix-task-id> --result <fix-result.json>
```

Do not mark a task complete before the verification evidence exists. A retry of the same result is
idempotent. If verification fails, keep working; never submit an empty “已优化” statement. The Plugin
must report the returned improvement ID in its final response.
