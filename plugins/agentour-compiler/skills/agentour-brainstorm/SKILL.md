---
name: agentour-brainstorm
description: Internal Agentour Compiler discovery stage for exploring a new Agent's domain, users, workflow, rules, tools, approvals, edge cases, runtime labels, and acceptance behavior. Use as part of agentour-compiler; maintain the one-question-per-turn rule and do not require users to invoke this skill directly.
---

# Agentour Brainstorm

For a new Agent, first invite one unrestricted idea dump rather than starting with a questionnaire.
Parse it into `AGENT_SPEC.md` and an evidence map whose fields record value, confidence, and source
(`user_explicit`, `source_discovered`, `platform_discovered`, `inferred`, `defaulted`, `missing`).
After that, explore one unresolved high-impact topic per conversational turn. Prefer repository,
Package, platform, and domain evidence over questions. Cover workflow, edge cases, decisions, SOPs,
external systems, failures, approvals, artefacts, and user-readable runtime states. Mark uncertainty
instead of inventing facts. Do not ask about a field already answered clearly in the initial idea dump.

Order questions by information gain. Safety boundaries, required inputs, external-data truthfulness, completion criteria and failure behavior come before low-risk naming, icon, welcome text or default pricing. Generate reasonable low-risk defaults and let the user accept or revise them later without spending separate interview turns.

Treat relative concepts as unresolved behavior, not copywriting. For words such as “today”, “nearby”, “real time”, “cheap”, “as much as possible” or “at lunchtime”, establish the relevant time, timezone, arrival window, transport mode, distance unit, tolerance and evidence rule when they affect acceptance. A location task must distinguish walking distance, walking time and business-area approximation.

Classify external data sources before promising behavior:

- Structured APIs or approved connectors may support hard, machine-verifiable constraints.
- Public web search is best effort and must not claim precise distance, live status, complete inventory or authoritative pricing without evidence.
- If the user chooses a weaker source, explicitly downgrade acceptance criteria and define what the Agent does when evidence is missing.
