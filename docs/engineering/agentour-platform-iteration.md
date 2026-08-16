# agentour-codex-plugin 持续迭代门禁

版本：`2026-08-16.v2`

跨仓开发时，完整通用流程以 Agentour 主仓库的 `docs/engineering/multi-repository-iteration-playbook.md` 为准。本文件保证只检出 Plugin 时仍能执行客户端侧最低门禁。

## 修改前

- 完整阅读 Plugin manifest、Compiler skill、API client、Package validator 和目标平台正式契约。
- Plugin 是跨仓 API Consumer，不实现 Core/Drive/Forge/Tenant 服务端状态机，也不通过客户端绕过授权。
- 每个新命令都要固定 route、method、请求字段、Idempotency-Key、状态轮询、错误码和 checkpoint schema。

## 必须自动验证

```powershell
python scripts/validate_all.py
python -m unittest tests/test_plugin.py
```

还必须对目标 Core 候选执行真实外部门禁：Repository/Commit/PR/Source Revision/Build/Eval/Release 的正向流程、断线后恢复同一 Job、错误 Audience/Scope、撤权和服务故障。

## 安全与可恢复性

- Token 使用系统凭据存储或受限 fallback 文件，按测试/正式环境分离。
- Token、Cookie、Git credential、Secret 和远端 job payload 不得进入 Package、checkpoint、flight log 或回复。
- checkpoint 只允许稳定、脱敏、可恢复字段；Commit 变化必须使旧 checkpoint 失效。
- 网络中断后轮询同一个远端 Job，不重复创建 Build/Eval/Release。
- Idempotency-Key 必须稳定绑定一次业务操作，不跨操作复用。

## 候选与发布

- Plugin Commit 必须进入五仓候选清单，测试时明确目标 Core/Contract 版本。
- 候选记录分开保存 Plugin `SOURCE_HEAD`、`TESTED_REVISION` 和目标平台各服务 `RUNTIME_REVISION`，不得因本地 Plugin HEAD 最新就假设测试环境已同步。
- fixture 只验证客户端合同；真实平台未完成时状态保持 external gate。
- Marketplace/Plugin 版本、manifest 和 API client 版本保持一致；更新后按缓存失效与重新安装流程验证。

## 已知易错点

- 客户端 fixture 与服务端新必填字段漂移。
- 把 HTTP 200 当作 Build/Eval/Release 成功，没有等待结构化终态。
- 重试创建了第二个远端 Job，破坏幂等和计费。
- checkpoint 或日志保存了 Token、凭据或非必要完整响应。
- Plugin 测试通过，但实际目标 staging 仍运行旧 Core Commit。
