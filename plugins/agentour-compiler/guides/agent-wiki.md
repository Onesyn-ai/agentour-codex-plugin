# Agent Wiki package contract

Every created, reconstructed, or updated Agent Package contains `AGENT_WIKI.md`. This is the Agent's
initial and continuously maintained Wiki page. It travels with the source Package and follows the
Agent's visibility. It is distinct from all of the following:

- `README.md`, which is the concise operator/user guide;
- `RELEASE.md`, which is the compact version release note;
- the terminal Compiler flight recorder uploaded to the improvement center;
- `.agentour/compiler-state.json`, which is resumable local/remote workflow state.

## Required content

Write the Wiki from the complete evidence available in the current Compiler run: the user's
development conversation, `AGENT_SPEC.md`, conversion inventory/map when present, selected reference
materials, manifest, Package implementation, Smoke/Eval evidence, and verified publication facts that
already exist for an update baseline. Summarize; do not paste a raw transcript.

The Wiki must contain these exact H2 headings:

1. `Agent 目标`
2. `开发对话流程概要`
3. `AI 输入与输出`
4. `工作流与业务规则`
5. `涉及资料与知识来源`
6. `接口、工具与外部系统`
7. `审批、安全与权限边界`
8. `验证与验收依据`
9. `已知限制`
10. `更新记录`

Under `开发对话流程概要`, describe the end-to-end development conversation as a chronological,
business-readable summary: initial request, questions and user answers, decisions/defaults,
requirement changes, conflicts resolved, and the Compiler stages that turned those facts into the
Package. Clearly distinguish user-explicit facts from Compiler inferences/defaults.

Under `AI 输入与输出`, separately cover:

- the sanitized information supplied to the development AI;
- the specs, code, tests, manifests, reports, and release facts produced by the development AI;
- the final Runtime input contract;
- the final Runtime output/deliverable contract.

Under `涉及资料与知识来源`, list the title/type, purpose, authority, freshness expectations, and
whether the material is packaged, attached through the Agent Collection, or read live through an
approved connector. Never embed private reference contents merely to make the Wiki self-contained.

Under `接口、工具与外部系统`, use a compact table when useful. Record interface/tool name, direction,
business purpose, key input/output shape, identity/authorization boundary, side effects, retry or
idempotency behavior, and honest failure behavior. Do not include credentials, signed URLs, private
hostnames, or ephemeral tokens.

## Redaction and publication boundary

Because `AGENT_WIKI.md` is Package content and can become public, it must never contain:

- passwords, Tokens, Cookies, API keys, private keys, OAuth codes, signed URLs, or credential-store
  details;
- raw private attachment/reference contents, personal data not required for the Agent contract, local
  absolute paths, private repository URLs, or internal incident-only payloads;
- hidden system/developer prompts or unrelated conversation;
- unsupported conclusions presented as user decisions or verified facts.

Use stable business names instead of ephemeral platform IDs unless an immutable public identifier is
part of the Agent's actual integration contract. State `未提供`, `不适用`, or `无法从现有证据确认`
instead of inventing missing history.

## Create and reconstruct behavior

Start from `templates/AGENT_WIKI.md`, replace every placeholder, and write the first update entry for
the Package version. For reconstruction, describe which behavior came from source inspection and which
came from the current user conversation. Do not claim historical decisions that were not preserved.

Generate the draft before local validation so it is included in `package.lock`. After Validation,
Build, or Eval facts change, update the evidence/current-state sections, regenerate the lock, and rerun
affected validation before publishing. Do not mutate the frozen Package after publication merely to add
the resulting release-operation ID: report that terminal outcome in the separate flight recorder, then
record the prior release as verified baseline history when the Agent is next updated.

## Update behavior

Treat an existing `AGENT_WIKI.md` as durable Package history:

1. Read it before changing Package files.
2. Preserve all prior `更新记录` entries verbatim unless they contain a proven secret; redact only the
   secret and note the redaction without retaining its value.
3. Refresh the current-state sections so they describe the new version rather than accumulating stale
   contradictions.
4. Append one new version/date/operation entry covering the user's objective, concrete behavior and
   contract changes, compatibility/migration notes, and verification.
5. If the baseline predates this contract, create the Wiki and explicitly state that earlier
   development conversation history was unavailable; reconstruct only what source and release evidence
   proves.

Do not replace the Wiki with `RELEASE.md`, append a raw flight log, or rewrite old history to make the
latest run look cleaner.
