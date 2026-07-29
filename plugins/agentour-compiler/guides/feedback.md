# Agentour Compiler complete run recorder

Continuously record every Compiler run, then upload exactly one terminal Markdown report for later AI investigation. If deployment succeeds, the report is the complete run flight recorder. If deployment is genuinely blocked and cannot be completed, the report is a detailed blocker report produced from the same evidence. A temporary block that later recovers belongs only in the final complete run recorder and must not create a separate blocker entry. This is a flight recorder, not a user summary, verdict, root-cause analysis, or prescribed fix.

## File and visible title

Use a readable filename:

`<agent-readable-name>-<create|update|reconstruct>-完整运行现象记录-<YYYYMMDD-HHmm>.md`

Use this H1:

`# <Agent readable name> · <operation> · Agentour Compiler 完整运行现象记录`

For a terminal run that cannot be uploaded, use:

`# <Agent readable name> · <operation> · Agentour Compiler 阻塞报告`

Never use generic names such as `result.md`, `feedback.md`, or `问题梳理与优化意见清单.md`.

## Evidence rules

- Preserve every failure, block, timeout, retry, Package mutation, recovery, long wait, repeated validation, and successful terminal job.
- Record slow or opaque successful work too. `succeeded` does not erase latency, repeated polling, missing substage progress, or long heartbeat-only intervals.
- Separate every statement into:
  1. `已确认事实`: directly supported by a log, API response, file, state, timestamp, or Job.
  2. `可能相关但未确认`: temporal/behavioral association without proven causality.
  3. `本次运行无法确定`: missing platform data or source-level knowledge.
- Do not emit `root_cause`, `responsible_component`, `confirmed_bug`, `required_fix`, or mandatory P0/P1/P2 labels without direct proof. A later success after a change proves sequence, not unique causality.
- Preserve redacted raw error codes, dependency names/versions, Gate names, thresholds, Job states, and relevant excerpts.
- Never store or report tokens, API keys, secrets, private attachment content, personal data, internal credentials, or large Base64/binary values.

## Continuous recording requirement

Do not reconstruct the run from memory after publishing. During the run, continuously persist non-secret evidence in both `.agentour/compiler-state.json` and the remote Compiler Task:

- stage start/end/duration;
- platform and Contract context;
- local Node/pnpm/Eve versions and remote environment fields actually returned;
- every Package hash and changed-file list;
- every Validation, Build, Publish, model-probe, preflight, checkpoint, and feedback Job/API;
- status transitions, heartbeat samples, poll count, polling interval, longest unchanged state, progress fields, timeout, quota fields, Gates, redacted errors;
- user actions/choices and whether core requirements changed;
- predecessor/successor Job relationships;
- final evidence IDs.

Keep a chronological `flight_events` array. Use stable event IDs `E-001`, `E-002`, etc. Sample repeated identical polling responses instead of storing every response, but retain enough samples to establish status duration, heartbeat behavior, progress granularity, and transition time.

## Required report sections

The single report must contain, in this order:

1. 运行范围与上下文
2. 用户请求及需求变化记录
3. 最终发布结果
4. 完整阶段时间线
5. 全部失败、阻塞、超时和重试事件
6. Package、配置及哈希变化记录
7. Validation Job 记录
8. Remote Build Job 记录
9. Publish Job 记录
10. 跨事件先后关系与现象链
11. 用户参与及责任相关事实
12. 时间和配额观测
13. 已确认事实汇总
14. 可能相关但未确认的观察
15. 本次运行无法确定的信息
16. 最终成功证据
17. 脱敏原始错误汇总
18. 机器可读事件数据

Do not omit an empty required section. State `未观察到` or `平台未返回/本次无法确定`.

## Context fields

Record Plugin/version, platform name and URL type, Contract version, operation, Workspace ID, Compiler Task ID, Agent ID/readable name/version, visibility, reasoning and specialist models, Node, pnpm, Eve, Runtime Profile, final Package hash, and all remote environment facts returned. Mark unknowns explicitly; never guess.

## Timeline and latency

For every stage/Job record start, finish, duration, input/output hash, Job ID, terminal status, retry, user involvement, status-stall intervals, heartbeats, visible substage progress, poll count, polling interval, and known timeout.

Include a table:

| 阶段或 Job | 状态 | 开始 | 结束 | 持续秒数 | 已知超时 | 心跳 | 子阶段进度 | 轮询次数 |

For long jobs include representative samples such as `00s running`, `60s running + heartbeat`, and the terminal sample. Record longest unchanged status and longest heartbeat-only interval.

## Failure event format

Each failure/block/timeout/unexpected retry gets a separate section with:

- event identity, timestamps, stage, task/Job/Package/version/hash, predecessor/successor;
- execution conditions and known environment;
- command/API/Gate, remote-resource entry, quota_chargeable;
- exact status/error code/redacted excerpt/returned fields/durations/timeout/heartbeat/progress/polls;
- checks already passed;
- subsequent action, changed files/values, behavior/security/test changes, new hash;
- rerun results, recurrence/new error, successor Job;
- evidence boundary: confirmed, unconfirmed, unknown.

## Package mutations and Job graph

For every hash change list before/after hashes, files, factual purpose, key value changes, effect on business rules, UX, approval, permissions, Smoke/Evals, and rerun Gates. Never claim a test was not weakened; describe exactly what changed.

List all Jobs, including failed and superseded ones:

| 顺序 | Job ID | 类型 | Package 哈希 | 开始 | 结束 | 结果 | quota_chargeable | 后续动作 | 后继 Job |

Preserve cross-event chains and explicitly say that observed sequence alone does not prove a shared internal cause or unexpected platform behavior.

## User facts

Record initial request, later choices, contradictions, credential failures, approval rejection, cancellation/pause, Gate-bypass requests, dependency/environment mandates, and which steps required user action. If none caused failure, say so factually and add that this alone does not prove a platform or Plugin defect.

## Machine-readable appendix

End with one fenced JSON object using `report_schema_version: "1.0"`. Include `run`, `user_observations`, chronological `events`, and `final_evidence`. Each event includes sequence/time/stage/job/hash/status/Gate/error/quota/timing/timeout/heartbeat/stall/progress/polls/user-action/package-change/changes/verification-jobs/successor/confirmed-facts/unconfirmed-observations/unknowns.

Do not include speculative ownership or required-fix fields.

## Completion

Upload exactly one terminal report through the feedback API and retain the returned feedback ID in
local and remote Compiler state. Do not upload while a recoverable retry or repair is still in
progress. On success, upload the complete 18-section recorder with all recovered blocks preserved.
Only when no permitted action can advance the run, upload the detailed blocker variant; section 3
must state `未完成`, section 16 must state that no success evidence exists, and all available failed
Job evidence must remain present. The final user response reports the readable report filename,
feedback ID, final Validation/Build/Publish IDs, and total observed duration.

## Optimization deposits are separate from flight records

The 18-section Markdown explains what happened during a Compiler run. When work is driven by an
accepted feedback/fix task, also submit a structured optimization deposit after verification. It is
the durable answer to “what did we learn and what changed?” and must include analysis, summary,
concrete changes, verification evidence, and commit IDs. Use `agentour_api.py complete-fix`; the Plugin
must not ask a user to copy this data into the platform manually.
