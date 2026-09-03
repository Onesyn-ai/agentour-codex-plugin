# 会议纪要整理助手规范

| Spec 条款 | 设计决策 | 实现位置 | 验证方式 | 状态 |
|---|---|---|---|---|
| 纯文本办公 Agent | 仅处理用户会议原文 | payload/agent/instructions.md | Smoke 正常输入 | 已实现 |
| 缺失输入逐项追问 | clarify_until_ready + ask_question | agentour.json/instructions.md | Smoke 空输入 | 已实现 |
| 不产生外部副作用 | 无 tools/secrets/approval | agentour.json | Validator | 已实现 |
| 交付 Markdown 纪要 | 固定字段和行动项表 | instructions.md | Smoke/平台 Eval | 已实现 |
