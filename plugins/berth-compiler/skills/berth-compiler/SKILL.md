---
name: berth-compiler
description: Create a new Berth Agent or convert an existing Agent project into a validated Berth Package, including platform model discovery, user-readable runtime status configuration, migration fidelity analysis, and authenticated publishing. Use when the user asks to build, migrate, package, validate, or publish a Berth Agent.
---

# Berth Compiler

Build production-oriented Berth Packages from an idea, specification, or existing Agent repository.

## Non-negotiable rules

1. Determine whether this is a new Agent or an existing Agent conversion before generating files.
2. Require `BERTH_URL`. Never silently use localhost or infer a production URL.
3. Discover public model routes from `${BERTH_URL}/v1/models`, not an admin endpoint.
4. Do not publish remotely without explicit user authorization in the current task.
5. Never write `BERTH_TOKEN`, provider keys, cookies, or other secrets into generated files or logs.
6. A successful build does not prove migration fidelity.
7. Never silently remove an unsupported source capability.
8. User-facing status must use business language. Never expose `load skill`, tool internals, container paths, or system prompts.
9. `waiting_approval` is paused and waiting for the user; it is not “running”.
10. Preserve unrelated source changes and generate converted packages in a separate directory by default.

## Phase 0: target platform

Check the environment:

```bash
test -n "$BERTH_URL" || { echo "BERTH_URL is required" >&2; exit 2; }
python3 "${CODEX_PLUGIN_ROOT}/scripts/berth_api.py" models
```

Present enabled model routes and select one appropriate for the Agent. Do not request provider secrets; the platform owns provider configuration.

## Phase 1A: new Agent

Create `AGENT_SPEC.md` early and keep it current. Establish:

- Domain, users, job to be done, and consequences of mistakes.
- Three concrete inputs and the expected output/artefacts.
- Missing-input behavior and multi-turn questions.
- Deterministic tools versus model reasoning.
- External writes and approval points.
- Required knowledge, SOPs, rules, secrets, and external services.
- Visibility, pricing, model, name, icon, tags, greeting, and examples.

Ask only the highest-value unresolved question at a time. Do not ask for information already present in the repository or conversation. Before generating code, show a compact design summary and obtain confirmation when the user has not already authorized implementation.

## Phase 1B: existing Agent inventory

Before changing source files, inspect entrypoints, prompts, tools, skills, MCP servers, sub-agents, workflows, tests, examples, dependencies, environment variables, file I/O, network access, approvals, and artefacts.

Generate `.berth/conversion-inventory.json` using `templates/conversion-inventory.json`. Every material capability must be represented. Mark critical capabilities explicitly.

Generate `.berth/conversion-map.json`. Allowed statuses are:

- `preserved`
- `adapted`
- `reimplemented`
- `degraded`
- `unsupported`
- `removed` only with explicit user authorization

For every non-preserved item, record the reason, implementation change, and user impact.

## Phase 2: package design

Create packages under `packages/<agent-id>/` unless the repository defines another Berth package root.

Required structure:

```text
packages/<agent-id>/
├── berth.json
├── README.md
├── RELEASE.md
├── tests/smoke.yaml
└── payload/
    ├── package.json
    ├── pnpm-lock.yaml
    └── agent/
        ├── agent.ts
        ├── instructions.md
        ├── sandbox/sandbox.ts
        ├── tools/
        └── skills/
```

Use files under `${CODEX_PLUGIN_ROOT}/templates/` as the starting point. Preserve source business rules and output contracts. Replace framework-specific orchestration only where the Berth runtime provides an equivalent.

## Runtime activity configuration

Every loaded capability must have a user-facing label in `berth.json`:

```json
{
  "runtime_ui": {
    "startup_message": "正在启动合同审查助手…",
    "default_working_message": "正在分析合同内容…",
    "capabilities": {
      "contract-risk-review": {
        "display_name": "合同风险识别",
        "loading_message": "正在加载合同风险识别能力…"
      }
    }
  }
}
```

Labels must describe real work, remain understandable to non-developers, and avoid implementation terminology. Approval tools must explain what will happen, why approval is needed, and the impact.

## Phase 3: validation

Run:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/validate_package.py" packages/<agent-id>
```

Then run the package build and project-provided tests. Do not alter failing tests merely to make conversion appear successful.

Validation must cover:

- Manifest and file structure.
- Lockfile and runtime entrypoint.
- Model route and platform URL behavior.
- Runtime activity labels for every capability.
- Approval declarations and user-readable approval details.
- Smoke coverage.
- Accidental secret inclusion.
- Forbidden internal language in user-visible fields.

## Phase 4: fidelity verification

For migrations, build test cases from source tests, README examples, sanitized historical cases, and prompt-defined tasks. Cover normal, boundary, failure, multi-turn, attachment, approval, and artefact behavior when applicable.

Run the same cases against the source Agent and Berth Package when both are executable. Compare hard assertions, structured output, semantic result, required capability execution, approval boundaries, artefacts, errors, latency, and resource use.

Write `.berth/fidelity-report.json` using the report template. Bind it to the final Package SHA-256.

Grades:

- A: at least 90 and all critical assertions pass.
- B: 80–89 and all critical assertions pass; require confirmation.
- C: 60–79 or material degradation; fix before publishing.
- D: below 60 or a critical capability fails; block publishing.
- Unverified: source Agent cannot be run dynamically; require human confirmation.

A critical Skill, required Tool, attachment parser, approval boundary, required schema, or artefact failure always blocks automatic publication regardless of score.

## Phase 5: publishing

Confirm the destination URL, Agent ID, version, visibility, fidelity grade, and unsupported capabilities. Then use async publishing:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/berth_api.py" publish-async packages/<agent-id>
```

The script requires `BERTH_URL` and `BERTH_TOKEN`. Follow the returned job until completion. On failure, report the failing gate and logs, make a narrowly scoped fix, rerun validation, regenerate the Package hash and fidelity report, then publish a new version as required by the platform.

## Completion report

Report:

- Package path, Agent ID, version, model, visibility, and target platform.
- Validation gates and test results.
- Fidelity grade and critical assertion results.
- Preserved, adapted, degraded, unsupported, and unverified capabilities.
- Publish job and final platform status.

Never reduce this to “conversion successful” when material limitations remain.
