---
name: agentour-compiler
description: Automatically create, reconstruct, validate, fidelity-test, and publish Agentour Agents. Trigger when a user wants to invent an Agent, convert or refactor an existing Agent project, package Agents for Agentour, or upload Agents. This is the single user-facing entry; it internally uses brainstorm, grill-me, and validation stages and strictly asks only one question or choice per conversational turn.
---

# Agentour Compiler

Own the complete workflow. The user must not orchestrate skills, commands, phases, validation, or retries.

## Mandatory version check

Before asking the first workflow question, run:

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
| A | 本地服 | `http://127.0.0.1:8600` |
| B | 比赛服 | `https://agentour.ai` |

Never ask the user to type a URL. Never infer localhost for 比赛服.

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

### 1. Platform choice

The first unresolved question must be exactly:

> 请选择发布平台：A. 本地服；B. 比赛服。

Record the selected name and URL.

### 2. Developer token

First inspect the selected platform's saved credential:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/credential_store.py" status <local|competition>
```

If a token is stored, validate it immediately without asking the user. Only ask for a token when none is stored or the platform explicitly returns 401/403. After receiving a replacement, validate it and store it through `credential_store.py set <platform>`; the credential script automatically selects Windows Credential Manager, macOS Keychain, Linux Secret Service, WSL bridging, environment variables, or a permission-restricted fallback.

Validate immediately:

```bash
AGENTOUR_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <local|competition> verify-token
```

- Never print, pass as a command-line argument, persist in the project, commit, or include the token in a report.
- If validation fails, ask one question requesting a corrected token after the user checks that platform's console.
- Do not advance until `GET /v1/dev/me` succeeds.

### 3. Model discovery

After token validation, first fetch the platform contract, then models:

```bash
AGENTOUR_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <local|competition> contract
AGENTOUR_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <local|competition> models
```

The `models` command probes every model returned by the selected platform, removes failed models from `data`, sorts usable models by platform quality rank, and returns `recommended_model`. Unless the user explicitly names a model, requests a cost ceiling, or says to prioritize economy, always use `recommended_model`: the Plugin must never silently downgrade Agent quality to save cost. Economic tradeoffs belong to the developer. Inspect `filtered_unavailable` only for diagnostics. Use the contract's Smoke schema, Node/Eve versions, canonical model IDs, ignore rules, package limit, pricing unit, and runtime semantics. Run `model-probe <model>` once more immediately before generation and never use a model that fails.

### 4. Intent and source choice

Ask exactly:

> 这次是：A. 更新已发布的 Agent；B. 重构已有项目；C. 从零创建 Agent？

If the user already clearly requested create, update, reconstruct, or continue, do not ask again.
Create the matching local and remote Compiler Task immediately.

### 5A. Update an owned Agent

Call `GET /v1/dev/packages` and `/v1/dev/packages/update-intents`; match only Packages owned by the
validated developer identity. Exact ID continues; an exact name may continue after showing its
summary; fuzzy or multiple matches require one choice. A missing match must ask whether the name is
wrong or the user intended a new Agent—never silently create. Download the active immutable baseline,
inspect the highest SemVer, verify the archive hash, and perform a three-way comparison when they
differ. Preserve unaffected behavior and create a new immutable version. Recheck model availability,
examples, approvals, deliverables, Knowledge Contract, Smoke, Evals, and fidelity instead of inheriting
old claims blindly.

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

## Package generation

Create each Package under `packages/<agent-id>/` from bundled templates with `agentour.json`, `README.md`, `RELEASE.md`, `tests/smoke.yaml`, and a complete `payload/` Eve project.

Preserve source business rules, orchestration, tool contracts, approvals, attachment behavior, output schemas, artefacts, retry behavior, and user-visible flow. Every capability needs business-readable `runtime_ui` labels. Never expose `load skill`, internal paths, or system prompts. `waiting_approval` means paused and waiting, never running.

- Price in **积分** using `pricing.amount_credits`; never describe it as RMB cents.
- Use Smoke `schema_version: 1` and only `send`, `expect_tool`, `expect_contains`, `expect_approval`, and `expect_question`.
- Missing required input must use Eve `ask_question`, producing `input_requested`.
- Check Node and pnpm before dependency work. Require Node 24; never compile Node from source.
- Generate the lockfile with `pnpm install --lockfile-only`. Do not install `node_modules` in the project merely to create the lock.
- If a local build is needed, use a Linux temporary copy or the platform-compatible container helper, then delete the temporary build directory.
- Maintain `.agentour/compiler-state.json` with contract version, publish jobs, failed Gates, repairs, and results; never include tokens.

## Automatic validation and repair

Internally apply `agentour-validator` and run:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/validate_package.py" packages/<agent-id>
```

Static validation is not sufficient. Before asking visibility or publishing, every Package must pass both commands:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" build-test packages/<agent-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <local|competition> \
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

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <local|competition> remote-build packages/<agent-id>
```

Use the structured `gates` result to repair deterministic failures. Do not retry unchanged content blindly; retry only after a repair or for the single transient retry handled by the platform. Publish only when the remote Build status is `succeeded`.

On HTTP `429`, explain that the active/daily E2B quota is exhausted and wait rather than
mutating content or looping retries. Cached Build results are valid and consume no new quota.
Cancel a superseded or user-cancelled job with `cancel-build <job-id>` and confirm its terminal
status before starting another paid Build.

```bash
AGENTOUR_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <local|competition> publish-async packages/<agent-id> \
  --visibility <private|public>
```

Follow every job. On Gate failure, fix, bump the version when required, rebuild fidelity evidence, and retry. Finish with final platform status and Package identifiers.

## Required post-publish platform feedback

After every successful platform deployment, create `问题梳理与优化意见清单.md`. This is **not** an Agent defect report. Include only issues and improvement opportunities in:

- Agentour platform capability, APIs, Gates, runtime, sandbox, models, billing semantics, diagnostics, documentation, or console;
- Claude/Codex Plugin workflow, templates, validators, packaging, interview quality, defaults, guidance, fidelity process, or observability.

Use evidence from the full run: confusing questions, misunderstood intent, wrong defaults, contract drift, unnecessary dependencies, platform-only failures, retries, weak diagnostics, fidelity gaps caused by platform limitations, and manual work the Plugin should automate. Do not blame the generated Agent for ordinary domain-specific defects.

Read `guides/feedback.md` before writing the report.

The Markdown must contain run scope, successful publish result, prioritized P0/P1/P2 findings, evidence, root cause, and actionable recommendations. If no issue was found, upload a short report stating what was checked and that no platform/Plugin defect was observed.

Upload it with the same validated token to the selected platform:

```bash
AGENTOUR_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" \
  --platform <local|competition> feedback "问题梳理与优化意见清单.md" \
  --plugin-version "0.3.0" --operation <create|reconstruct> \
  --agent-id <agent-id> --publish-job <job-id>
```

Report the feedback ID to the user. Feedback upload is part of successful completion, not an optional suggestion.
