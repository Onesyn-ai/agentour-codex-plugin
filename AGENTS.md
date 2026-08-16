# agentour-codex-plugin 项目不可变规范

除非用户明确修改，所有在本仓库工作的自动化代理都必须遵守以下规则。

## 多仓迭代

- 涉及 Agentour Core、Company Drive、Forge 或 Tenant 的契约、联调、部署和验收时，必须完整阅读 `docs/engineering/agentour-platform-iteration.md`（版本 `2026-08-16.v2`），并在可访问 Agentour 主仓库时继续阅读其完整多仓 Playbook。
- Plugin 是 API Consumer；服务端新字段、状态机或权限变化必须同步增加客户端正向、负向和真实候选测试。
- 重复发生或显著耗时的问题必须优先固化为 validator、unittest、checkpoint 合同或发布校验。

## 边界与安全

- 不在 Plugin 内实现或绕过 Core、Drive、Forge、Tenant 的服务端权威。
- Token、Cookie、Git credential、Secret、密码和私钥不得进入 Package、checkpoint、flight log、提交或回复。
- checkpoint 只保存稳定脱敏字段；网络失败恢复同一远端 Job，不重复创建或重复计费。

## Git 与完成标准

- 默认从目标稳定开发分支创建 feature 分支；频繁小步提交并可推送 feature 备份。
- 未获用户明确授权，不得合并稳定分支、发布 Marketplace 版本或触发部署。
- 必须运行 `python scripts/validate_all.py` 和 `python -m unittest tests/test_plugin.py`；跨仓功能还必须针对精确 Core 候选进行真实外部门禁。
- fixture 通过只能证明客户端合同，不能证明平台 `release-ready`。
