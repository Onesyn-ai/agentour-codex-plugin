---
name: berth-compiler
description: Automatically create, reconstruct, validate, fidelity-test, and publish Berth Agents. Trigger when a user wants to invent an Agent, convert or refactor an existing Agent project, package Agents for Berth, or upload Agents. This is the single user-facing entry; it internally uses brainstorm, grill-me, and validation stages and strictly asks only one question or choice per conversational turn.
---

# Berth Compiler

Own the complete workflow. The user must not orchestrate skills, commands, phases, validation, or retries.

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
| B | 比赛服 | `http://61.29.254.146` |

Never ask the user to type a URL. Never infer localhost for 比赛服.

## Mandatory state machine

Persist non-secret progress in `.berth/compiler-state.json` so a new Thread can resume. Never store the token.

### 1. Platform choice

The first unresolved question must be exactly:

> 请选择发布平台：A. 本地服；B. 比赛服。

Record the selected name and URL.

### 2. Developer token

The next unresolved question asks only for the selected platform's `bt_` developer token. Explain that it is used for validation and publishing and will not be written to files.

Validate immediately:

```bash
BERTH_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/berth_api.py" \
  --platform <local|competition> verify-token
```

- Never print, persist, commit, or include the token in a report.
- If validation fails, ask one question requesting a corrected token after the user checks that platform's console.
- Do not advance until `GET /v1/dev/me` succeeds.

### 3. Model discovery

After token validation, automatically fetch `GET /v1/models`:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/berth_api.py" \
  --platform <local|competition> models
```

Select a suitable enabled model based on complexity, risk, context needs, and cost. Ask about the model only if alternatives create a material business tradeoff; that turn must contain only that choice.

### 4. Source choice

Ask exactly:

> 这次是：A. 重构已有 Agent；B. 从零发明一个 Agent？

### 5A. Existing Agent inventory

Inspect before asking about anything discoverable. Inventory entrypoints, Agents, prompts, skills, tools, MCP servers, sub-agents, workflows, routing, tests, examples, dependencies, environment variables, external services, files, attachments, approvals, artefacts, retries, and failures.

If multiple Agents exist, ask one scope choice:

> 检测到多个 Agent。你希望：A. 合成一个 Agent；B. 分别转换并上传全部 Agent；C. 只转换其中一部分？

- For C, the next turn asks only which Agents to include; multi-select is allowed because it resolves one scope decision.
- For A, preserve every source Agent's role, routing, workflow, tools, and boundaries in one Package.
- For B, create one Package and fidelity report per source Agent.

Generate `.berth/conversion-inventory.json`, `.berth/conversion-map.json`, and `.berth/fidelity-report.json`. Mark every capability `preserved`, `adapted`, `reimplemented`, `degraded`, `unsupported`, or `removed` only with explicit authorization.

### 5B. New Agent discovery

Create `AGENT_SPEC.md` immediately. Internally apply `berth-brainstorm` and `berth-grill-me`. Interview one question per turn across domain, exact job, user, error consequences, inputs, outputs, workflow, missing information, ambiguity, tools, model judgment, external systems, secrets, approvals, SOPs, edge cases, forbidden actions, runtime labels, pricing, identity, and examples.

Do not implement until the spec can reproduce the intended workflow. Do not ask for a separate implementation confirmation when creation was already authorized.

## Package generation

Create each Package under `packages/<agent-id>/` from bundled templates with `berth.json`, `README.md`, `RELEASE.md`, `tests/smoke.yaml`, and a complete `payload/` Eve project.

Preserve source business rules, orchestration, tool contracts, approvals, attachment behavior, output schemas, artefacts, retry behavior, and user-visible flow. Every capability needs business-readable `runtime_ui` labels. Never expose `load skill`, internal paths, or system prompts. `waiting_approval` means paused and waiting, never running.

## Automatic validation and repair

Internally apply `berth-validator` and run:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/validate_package.py" packages/<agent-id>
```

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

```bash
BERTH_TOKEN="<token>" python3 "${CODEX_PLUGIN_ROOT}/scripts/berth_api.py" \
  --platform <local|competition> publish-async packages/<agent-id> \
  --visibility <private|public>
```

Follow every job. On Gate failure, fix, bump the version when required, rebuild fidelity evidence, and retry. Finish with final platform status and Package identifiers.
