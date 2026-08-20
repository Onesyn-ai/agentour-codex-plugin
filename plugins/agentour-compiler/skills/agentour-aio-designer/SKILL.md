---
name: agentour-aio-designer
description: Compile a complete Agentour AIO Package inside the platform-managed E2B Codex design workspace, validate the workflow, and write the final result for the control plane. Use only inside the Agentour hosted E2B design template.
---

# Agentour AIO Designer

This skill is part of the existing `agentour-compiler` Plugin. An AIO is a workflow Package, not a
one-shot model response. The Plugin owns the files inside the isolated workspace; the Core control plane
owns task state, revisions, events and Proposal updates. The Plugin never calls task mutation APIs.

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

## Submit

Write the final object to `/workspace/design/result.json`. Core downloads and validates this file, records
events, updates the versioned workspace and applies Proposal compare-and-swap. Never invoke a local or
external executor, never switch modes, and never publish; publishing only occurs after browser confirmation.
