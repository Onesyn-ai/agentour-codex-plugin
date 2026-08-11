---
name: agentour-aio-designer
description: Claim and continuously compile a complete Agentour AIO Package workspace, synchronize revisions and progress with the browser, validate the workflow, and submit the package result. Use when a prompt contains an AIO Design Task ID and claim token or when running inside the Agentour hosted E2B design template.
---

# Agentour AIO Designer

This skill is part of the existing `agentour-compiler` Plugin. An AIO is a workflow Package, not a
one-shot model response. The Plugin owns the complete progressive design workspace; the browser is a
synchronized remote UI. The Plugin never mutates a Proposal directly.

## Claim and restore

For a user-owned local task, claim it once:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  claim-aio-design <task-id> <claim-token>
```

Download and materialize the versioned workspace:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  pull-aio-workspace <task-id> packages/<aio-id>
```

On resume, pull the workspace again and reconcile `.agentour/remote-workspace.json`. Never substitute
another task or Proposal revision. After every coherent change, synchronize the whole text workspace:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  sync-aio-workspace <task-id> packages/<aio-id> --stage requirements \
  --message "需求契约已更新" --progress 20
```

## Execution contract

Maintain a complete Package containing `agentour.json`, `README.md`, `RELEASE.md`, `aio/requirement.json`,
`aio/acceptance-cases.json`, `aio/agents.json`, `aio/teams.json`, `aio/nodes.json`, `aio/routes.json`,
`aio/contracts.json`, `aio/human-gates.json`, `aio/test-cases.json`, `aio/validation.json`,
`workflows/main.json`, and `.agentour/design-state.json`. Files appear progressively and must remain
internally consistent at every synchronized revision.

Use only Agent IDs from `aio/agents.json`. During exploration, progressively update requirements and
acceptance cases. During architecture, generate one to three candidates with IDs `reuse`, `balanced`,
and `quality`, then materialize the selected design as `workflows/main.json`.

Output only:

```json
{"candidates": [], "unavailable_slots": []}
```

Candidates must contain only the declared teams, nodes, routes, gaps, coverage, rationale and estimate
fields expected by Agentour. Never output diagram coordinates. Missing capability must be a `gap`; never
invent an Agent. Preserve complete responsibilities, input/output contracts, acceptance coverage, human
review after worker delivery, user input and final output semantics.

Before submission, validate JSON parsing, allowed Agent IDs, unique IDs, root team, route endpoints,
reachability, acyclicity and contract closure. Perform at most one directed repair pass.

## Progress

Report real milestones, not simulated timers:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  report-aio-design <task-id> --type candidate_started --stage generating \
  --message "正在生成平衡方案" --progress 35
```

Report `inventory_loaded`, `candidate_started`, `candidate_validating`, and `candidate_validated` as they
happen. Do not include chain-of-thought; messages are concise user-visible status.

## Submit

Write the final object to a UTF-8 JSON file and submit it:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  submit-aio-design <task-id> --result .agentour/aio-design-result.json
```

The platform is authoritative. A workspace 409 means another revision exists: pull, reconcile semantic
changes, and sync again. A result 409 means the Proposal changed; preserve the Package but do not
overwrite it. A 422 allows one directed repair. Never silently switch executor mode. Publishing only
occurs after browser confirmation and uses the normal Agentour Package publisher.
