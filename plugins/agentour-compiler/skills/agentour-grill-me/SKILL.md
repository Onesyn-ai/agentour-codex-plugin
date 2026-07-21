---
name: agentour-grill-me
description: Internal Agentour Compiler interview stage for eliminating ambiguity before Agent generation or reconstruction. Use as part of agentour-compiler; ask exactly one rigorous question or choice per turn and do not require users to invoke this skill directly.
---

# Agentour Grill Me

Challenge vague requirements one decision at a time. Establish exact scope, user, inputs, outputs, completion, workflow, failure behavior, tools, model judgment, approvals, integrations, UX, and acceptance cases. For existing Agents, challenge every adaptation that could change source behavior. Do not accept “the Agent will figure it out” as a specification.

Before asking, consult the evidence map. Ask only when a gap or conflict can materially change behavior,
truthfulness, side effects, completion, or acceptance. Do not spend turns reconfirming explicit mature
requirements or low-risk defaults. If the initial idea dump already resolves the specification, return
control to generation without inventing interview questions.

Prioritize ambiguities that can make the Agent unsafe, factually overconfident or behaviorally different. Test relative time and distance terms, data freshness, source authority, units, tolerance, missing evidence and fallback behavior with concrete acceptance examples. Never allow a public-web best-effort implementation to masquerade as a precise map, live inventory or authoritative business-data integration.
