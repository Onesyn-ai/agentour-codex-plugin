# Tenant Plugin Profile 设计与现有网络接口审计

状态：设计稿，不改变当前 Plugin 行为  
适用代码：`agentour-codex-plugin` `0.9.5+codex.20260828143402`  
审计日期：2026-08-29

## 1. 目标

租户内部用户安装的仍是 Agentour 官方 Codex Plugin，但使用时只感知租户自己的产品：

- 安装说明展示租户名称、品牌和支持入口；
- 首次启动只发现该租户提供的环境，不显示 Agentour 测试服或正式服；
- 登录和授权由租户控制，Agentour 不要求内部用户拥有平台账号；
- 模型、Agent、Forge、Drive、Compiler、反馈和诊断均经过租户提供的统一网关；
- 租户可附带一个 Skill，补充本租户的业务规则和使用方式；
- Plugin 更新仍来自 Agentour 官方 Marketplace，安全修复不会被租户 Fork 隔断。

本文只定义方案和迁移边界，不实现或启用租户模式。

## 2. 结论

用户提出的方向成立，但不应让 Core 保存几十个可任意配置的接口地址，也不能只靠 Skill 改写 Plugin 行为。推荐的最小完整方案是：

```text
租户专属安装链接
       |
       v
Agentour Core 发布的签名 Tenant Plugin Profile
       |
       +-- 品牌、环境、Gateway Origin、OAuth 元数据、能力、Skill 摘要
       |
       v
租户 Plugin Gateway（租户实现、固定合同）
       |
       +-- 租户身份系统
       +-- 租户模型与 Agent 权限
       +-- Agentour 的租户级 Core / Drive / Forge API
       +-- 租户自己的反馈与支持系统
```

Core 的租户管理空间只配置少量稳定信息：Gateway Origin、品牌、支持地址、可选 Skill 和启用状态。Core 校验后生成、签名并托管 Profile。Plugin 只信任 Agentour 固定公钥签名的 Profile，并只向 Profile 中的单一 HTTPS Gateway Origin 发送业务请求。

租户的登录实现可以任意选择 Logto、Auth0、自建 OIDC 或已有账号系统，但对 Plugin 必须呈现固定的 OAuth 2.1 Authorization Code + PKCE 合同。所谓“任意实现”应发生在 Gateway 后面，不能让 Plugin 下载和执行租户提供的认证代码。

## 3. 为什么不能只配置一个 Skill

Skill适合表达：

- 租户内部业务名词；
- 生成 Agent 时必须遵守的内部规则；
- 推荐工作流和支持渠道；
- 不涉及权限的操作提示。

Skill 不适合承载安全配置：

- Skill 是模型可读的指令，不是可信网络策略；
- Skill 文本可能被修改、忽略或被其他上下文覆盖；
- Skill 不能安全保存 Client Secret、Token 或长期租户凭据；
- Skill 无法证明接口属于哪个租户，也无法约束重定向、Token Audience 和 Scope；
- 让 Skill 指示脚本访问任意 URL 会形成 SSRF、凭据外发和供应链风险；
- Skill 更新与凭据撤销不是同一个生命周期。

因此，Skill 只能是签名 Profile 引用的可选扩展。Plugin 在校验 Profile 签名、租户 ID、版本、有效期和 Skill 哈希后才加载它；即使 Skill 内容恶意或错误，也不能改变 Gateway Origin、OAuth 端点、Token 存储分区、允许的 API 能力或发布门禁。

## 4. 当前 Plugin 的实际网络面

### 4.1 固定平台和通用传输

当前 `agentour_api.py` 固定两个平台：

| 逻辑环境 | Origin |
| --- | --- |
| `test` | `https://test.agentour.ai` |
| `production` | `https://agentour.ai` |

除更新检查、Git 和 Lark CLI 外，业务请求都经过 `base_url()`、`request()` 或 `authenticated()`。GET/HEAD 对传输错误和 502/503/504 最多尝试四次；写请求不自动重放。认证响应 401 会删除当前环境凭据。

代码中已有 `tenant_access_token_v1`：凭据可携带 `api_origin`、`tenant_id`、短期 Token、Scope 和过期时间，并让 `base_url()` 指向该 Origin。它目前不是完整租户接入方案，原因包括：

- 没有可信 Profile 的发现、签名、有效期和撤销；
- 没有安装链接到租户配置的绑定；
- 没有标准租户 OAuth 登录流程，只能预先写入一个短期 Token bundle；
- 凭据仍只按 `test` / `production` 两槽保存，多个租户会互相覆盖；
- 没有能力版本协商、品牌、Skill 校验或 Gateway 合同；
- `PLATFORMS[...]` 的展示名称仍可能暴露 Agentour 环境语义。

实现时应一次性迁移并删除该历史形态，不要长期同时兼容旧 bundle 与 Profile 会话。

### 4.2 OAuth、身份和撤销

| Method | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/auth/oidc-config` | 读取公开 Issuer / Audience 配置并参与身份校验 |
| GET | `/v1/plugin/identity` | 使用 Access Token 获取 subject、issuer、scope 等身份事实 |
| GET | `/v1/oauth/authorize` | 浏览器 Authorization Code + PKCE 授权 |
| POST | `/v1/oauth/token` | authorization_code 和 refresh_token 交换 |
| POST | `/v1/oauth/revoke` | 切换账号时撤销 Refresh Token |
| GET | `/v1/dev/me` | 验证开发者身份并显示当前账号 |

当前公共客户端 ID 是 `agentour-codex-plugin`，基础 Scope 是：

```text
openid profile offline_access
agent:read agent:write
repository:read repository:write
drive:file:read drive:file:write
```

PR 合并另需 `repository:release`，发布另需 `agent:publish`。凭据存入 Windows Credential Manager、macOS Keychain 或 Linux Secret Service，测试服与正式服分槽。

### 4.3 平台合同、模型和预检

| Method | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/models?modality=chat` | 获取候选模型 |
| POST | `/v1/dev/model-probe/{model}` | 逐个验证模型真实可用性 |
| GET | `/v1/dev/compiler-contract` | Compiler Schema、Runtime、限制和 Smoke 合同 |
| GET | `/v1/dev/build-preflight` | 远程 Build 前置条件 |

租户不能只替换模型列表而沿用平台探测，否则可能泄露平台模型或出现“列表可见但不可运行”。Gateway 必须同时实现模型列表和探测，并根据内部用户身份完成过滤。

### 4.4 Compiler Task、Package 和修复队列

| Method | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/dev/compiler-tasks?active={bool}` | 列出 Compiler Task |
| POST | `/v1/dev/compiler-tasks` | 创建可恢复 Task |
| GET | `/v1/dev/compiler-tasks/{id}` | 读取 Task 和 revision |
| PATCH | `/v1/dev/compiler-tasks/{id}` | 乐观锁同步状态与飞行记录 |
| POST | `/v1/dev/compiler-tasks/{id}/package` | 保存 Package checkpoint |
| GET | `/v1/dev/compiler-tasks/{id}/package` | 恢复 Package checkpoint |
| POST | `/v1/dev/packages/update-intents` | 解析更新目标 |
| GET | `/v1/dev/fix-tasks[?kind=...]` | 读取已接受的修复任务 |
| POST | `/v1/dev/fix-tasks/{id}/claim` | 领取修复任务租约 |
| POST | `/v1/dev/fix-tasks/{id}/complete` | 提交验证结果 |

`/v1/dev/fix-tasks?limit=200` 还在 bootstrap 中作为可选旧能力：只有 404 会被当作空集合。Profile 应显式声明能力，不应继续用 404 猜测版本。

### 4.5 Validation、Build 和轮询

| Method | 路径 | 用途 |
| --- | --- | --- |
| POST gzip | `/v1/dev/validate-package` | 上传干净 Package 并创建 Validation |
| GET | `/v1/dev/validate-jobs/{id}` | 轮询 Validation |
| POST gzip | `/v1/dev/builds` | 创建 Remote Build |
| GET | `/v1/dev/builds/{id}` | 轮询或恢复 Build |
| POST | `/v1/dev/builds/{id}/cancel` | 取消已被替代的 Build |

创建调用依赖服务端幂等和计费语义，Gateway 不得把轮询超时转换成新建 Job。

### 4.6 Forge Repository 和 Git

| Method | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/forge/repositories?limit=&cursor=` | 列出 Repository |
| POST | `/v1/forge/repositories` | 创建 Repository |
| GET | `/v1/forge/repositories/{id}` | 读取创建状态和基础信息 |
| POST | `/v1/forge/git-credentials` | 获取一次性、短期 Git 凭据 |
| GET | `/v1/dev/repositories/{id}` | 读取开发者合同下的 Repository 摘要 |
| POST | `/v1/dev/repositories/{id}/change-sets` | 为固定 Commit 创建或恢复 PR |
| GET | `/v1/dev/repositories/{id}/pull-requests/{number}` | 读取 PR 和 Review 事实 |
| POST | `/v1/dev/repositories/{id}/pull-requests/{number}/merge` | 按策略合并固定 PR head |

Plugin 从 Git 凭据响应取得无凭据 HTTPS `clone_url`、username 和 credential，然后通过临时 AskPass 环境执行 `git clone/fetch/push`。因此还有一条不经过 Gateway 的数据链路：

```text
Plugin -> Gateway 获取一次性凭据 -> clone_url 对应的 Forge Git 服务
```

Profile 必须限定允许的 `git_origins`。Plugin 收到的 `clone_url` 必须为 HTTPS、无内嵌凭据且 host 命中允许列表；临时凭据不得写入命令行、日志、checkpoint 或 Skill。

### 4.7 Source Build、Eval 和 Release

| Method | 路径 | 用途 |
| --- | --- | --- |
| POST | `/v1/dev/repositories/{id}/source-revisions` | 为固定 Commit 创建 Source Revision |
| GET | `/v1/dev/source-revisions/{id}` | 读取 Source Revision |
| POST | `/v1/dev/source-revisions/{id}/builds` | 创建 Source Build |
| GET | `/v1/dev/source-builds/{id}` | 恢复和轮询 Source Build |
| POST | `/v1/dev/source-revisions/{id}/eval-runs` | 创建 Source Eval |
| GET | `/v1/dev/source-eval-runs/{id}` | 恢复和轮询 Source Eval |
| POST | `/v1/plugin/agents/{agent_id}/releases` | 执行 Core / Forge / Drive 统一发布事务 |

Release 的完成证据包含 Repository、Drive Snapshot、Forge Tag/Release 和 Core Version。租户 Gateway 可以代理这些调用，但不能在客户端重新实现跨服务事务。

### 4.8 Agent 与 Drive 相关调用

| Method | 路径 | 用途 |
| --- | --- | --- |
| POST | `/v1/plugin/agents/{agent_id}/collection:ensure` | 确保 Agent 的 Drive Collection，要求 `agent:write` 与 `drive:file:write` |
| POST binary | `/v1/plugin/agents/{agent_id}/files:upload` | 上传参考文件，使用文件名与幂等请求头 |
| POST | `/v1/plugin/agents/{agent_id}/releases` | 发布时统一写入 Agent、Drive 和 Forge |

当前 Plugin 没有通用网盘浏览、删除、重命名、预览等调用。它只使用 Agent 编译所需的 Drive 能力。租户 Profile 不应宣称 Plugin 已覆盖完整 Drive UI API。

### 4.9 反馈、诊断和本地状态

| Method | 路径 | 用途 |
| --- | --- | --- |
| POST | `/v1/dev/feedback` | 上传一次终态脱敏运行报告 |
| PATCH | `/v1/dev/compiler-tasks/{id}` | 同步脱敏飞行记录摘要 |
| POST | `/v1/dev/fix-tasks/{id}/complete` | 提交反馈驱动修复的证据 |

本地 `.agentour/compiler-state.json`、Forge checkpoint 和 flight recorder 也属于协议的一部分，但不得保存 Token、Cookie、Git credential、任意完整远端 payload 或 Profile 内的敏感字段。

### 4.10 更新和第三方出口

这些不是租户业务 API，但必须纳入白标审计：

| 出口 | 用途 | 租户模式策略 |
| --- | --- | --- |
| `raw.githubusercontent.com/Onesyn-ai/.../plugin.json` | 检查官方 Plugin 版本 | 保持官方来源，不交给 Skill 改写 |
| `codex plugin marketplace upgrade agentour-platform` | 更新 Marketplace | 保持官方来源 |
| `codex plugin add agentour-compiler@agentour-platform` | 重装 Plugin | 保持官方来源 |
| `api.github.com/repos/larksuite/cli/releases/latest` | Lark CLI 版本检查 | 作为第三方依赖明确披露 |
| `npm view @larksuite/cli` / `npx` | Lark CLI 检查和安装 | 作为第三方依赖明确披露 |
| 模型生成出的 Agent Runtime API | Agent 运行期模型调用 | 属于生成 Package，不属于 Plugin 控制面 Profile |

官方更新源不应被租户 Gateway 接管，否则租户可向内部用户分发被篡改的 Plugin。品牌白标不等于供应链来源白标。

## 5. Tenant Plugin Profile

### 5.1 专属安装链接

Core 租户管理空间生成类似链接：

```text
https://agentour.ai/plugin/install?tenant_profile=tp_xxx
```

测试环境使用测试服域名。参数只包含不可猜测但不作为秘密的 Profile ID，不包含租户凭据、用户 Token、Skill 内容或任意回调 URL。页面返回租户定制的安装说明，并展示：

- 租户品牌与用途；
- 官方 Marketplace 安装命令；
- 一次性的“绑定租户配置”命令或可复制配置片段；
- Profile 指纹和环境；
- 更新、换号、撤销和支持说明。

更理想的安装命令形态为：

```text
python <PLUGIN_ROOT>/scripts/tenant_profile.py enroll \
  --profile-url https://agentour.ai/v1/plugin-profiles/tp_xxx
```

Profile URL 不是登录凭据。首次真正使用时才打开租户授权页。

### 5.2 Profile 建议结构

```json
{
  "schema_version": "1.0",
  "profile_id": "tp_xxx",
  "tenant_id": "ten_xxx",
  "environment": "production",
  "display_name": "示例租户 Agent 平台",
  "gateway_origin": "https://agents.example.com",
  "oauth": {
    "authorization_path": "/oauth/authorize",
    "token_path": "/oauth/token",
    "revoke_path": "/oauth/revoke",
    "client_id": "agentour-codex-plugin",
    "scopes": ["openid", "profile", "offline_access", "agent:read"]
  },
  "contract_path": "/v1/plugin/contract",
  "git_origins": ["https://forge.example.com"],
  "capabilities": {
    "models": "1.0",
    "compiler": "1.0",
    "forge": "1.0",
    "drive_agent_files": "1.0",
    "feedback": "1.0"
  },
  "skill": {
    "url": "https://agentour.ai/v1/plugin-profiles/tp_xxx/skill",
    "sha256": "...",
    "required": false
  },
  "support_url": "https://support.example.com/agents",
  "issued_at": "2026-08-29T00:00:00Z",
  "expires_at": "2026-09-28T00:00:00Z",
  "revision": 4,
  "key_id": "agentour-profile-2026-01",
  "signature": "base64url..."
}
```

Profile 不包含 Secret。端点原则上采用一个 Origin 加固定相对路径。确实需要路径差异时，由版本化 contract 返回有限的 route ID 到相对路径映射；禁止配置任意完整 URL，禁止每个端点指向不同 host。

### 5.3 签名和缓存

- 使用非对称签名；私钥只在 Core 服务端，Plugin 固定或轮换信任公钥；
- 签名覆盖规范化后的完整 Profile，包含 tenant、environment、origin、能力、Skill 哈希和有效期；
- Plugin 本地仅保存签名 Profile、ETag、最后验证时间和租户标识；
- 每次启动进行条件请求，网络短暂失败时可在很短的明确宽限期使用未过期缓存；
- Profile 过期、撤销、签名错误、租户 ID 变化或 origin 变化均 fail closed；
- Gateway 重定向到其他 Origin 时拒绝携带 Authorization；
- Profile 更新不能静默放宽 Scope，新增高权限 Scope 必须重新授权。

## 6. 租户 Plugin Gateway 合同

### 6.1 为什么使用一个 Gateway

逐接口配置看似灵活，实际会产生几十个 URL、CORS/证书差异、凭据误发、路径漂移和无法验收的问题。单一 Gateway 仍允许租户后端采用任何技术栈，也允许它把请求转发到多个内部服务，但 Plugin 只面对一个安全边界。

Gateway 的职责：

1. 用租户自己的账号系统认证内部用户；
2. 将内部用户映射为稳定、不可变的 subject；
3. 返回该用户可用的模型、Agent、Repository 和权限；
4. 使用服务器端租户凭据调用 Agentour Core / Drive / Forge；
5. 把租户内部数据转换为 Plugin 固定合同；
6. 保持 Idempotency-Key、Job ID、错误 envelope 和请求关联 ID；
7. 实施限流、撤销、审计和租户内部支持策略。

Gateway 不应把 Agentour 租户长期凭据、Core 短 Token 或 Forge Git credential 暴露给浏览器。Plugin 可以持有内部用户经 OAuth 获得的短 Access Token 和旋转 Refresh Token；它们只用于 Gateway。

### 6.2 固定合同而非任意实现

租户可以任意实现自己的登录页面、账号数据库和授权规则，但应适配以下标准边界：

- OAuth 2.1 Authorization Code + PKCE；
- 固定 identity response；
- 固定模型、Compiler、Forge、Build、Eval、Release response schema；
- 统一错误 envelope：code、message、request_id、correlation_id、stage、target_service、retryable；
- 写操作传递并遵守 Idempotency-Key；
- Job 创建和 Job 查询严格分离；
- Scope 不足返回 403，不用 404 或空列表伪装；
- 能力未启用由 Profile/contract 明示，不靠失败探测。

如果某租户只需要模型和 Agent 创建，可只声明相应能力。Plugin 应隐藏不可用流程，而不是调用后再报错。

## 7. 白标边界

租户模式中应替换：

- 用户可见的平台名、环境名、授权页和支持入口；
- 服务器选择列表，只展示当前 Profile 声明的租户环境；
- 模型、Agent、Repository 和反馈数据来源；
- 租户 Skill 提供的业务说明。

应保留并如实说明：

- Plugin 软件由 Agentour 官方 Marketplace 发布和更新；
- Package / Compiler contract 的版本来源；
- 安全错误中的稳定技术代码；
- 必要时的 Agentour 基础设施处理声明。

不应通过隐藏供应链来源来制造“租户自行开发 Plugin”的假象。

## 8. 凭据和环境隔离

现有按 `test` / `production` 保存凭据不够。新存储键至少包含：

```text
service = agentour-compiler
account = tenant-profile:<profile_id>:<environment>:<subject-slot>
```

规则：

- 一个 Profile、一个环境、一个账号槽独立保存；
- 切换账号先调用该 Gateway 的 revoke，再删除该槽；
- 租户 Profile 不能读取或覆盖 Agentour 平台凭据；
- testing 和 production Profile 即使 tenant_id 相同也不得共享 Token；
- Profile 缓存不保存 Token，Token bundle 不保存租户长期凭据；
- 日志只记录 profile_id、tenant_id、environment、request ID 和脱敏状态；
- 禁止环境变量或 Skill 静默覆盖已签名 Gateway Origin。

## 9. Skill 生命周期

租户管理空间可以编辑 Markdown Skill，Core 负责：

1. 校验大小、编码和禁止的敏感内容；
2. 生成不可变 revision 和 SHA-256；
3. 在 Profile 中绑定 URL、hash 和 required 标志；
4. 保留上一版本以支持 Profile 回滚；
5. 撤销 Profile 时同时停止分发 Skill。

Plugin 下载后再次校验 hash，保存到 Profile 专属目录。Skill 不能：

- 定义或覆盖网络 Origin；
- 要求用户粘贴 Token、Cookie 或租户凭据；
- 跳过 Build、Eval、发布或权限门禁；
- 执行未由 Plugin 固定代码允许的任意脚本；
- 修改官方更新源。

不需要租户 Skill 时完全省略。不要为“可能未来会用”生成空 Skill 或占位说明。

## 10. Core 租户管理空间配置

建议只提供一个“Plugin 接入”页：

| 配置 | 必需 | 说明 |
| --- | --- | --- |
| 显示名称 | 是 | Plugin 和安装说明中展示 |
| Gateway Origin | 是 | 单一 HTTPS Origin |
| 环境名称 | 是 | 例如“公司生产环境”，不是 Agentour 正式服 |
| 支持地址 | 否 | 内部帮助台或文档 |
| 能力开关 | 是 | 只能选择平台已支持的合同能力 |
| OAuth Client ID / Scope | 是 | 公共客户端配置，不含 Client Secret |
| Git Origin allowlist | Forge 启用时 | 限制一次性凭据可使用的 Git host |
| 租户 Skill | 否 | Markdown、revision、hash |
| 启用/撤销 | 是 | 控制 Profile 是否可注册和刷新 |

Core 应主动探测 Gateway 的 TLS、contract、OAuth 元数据、错误 envelope 和能力完整性，通过后才允许发布 Profile。不要提供“高级模式：逐条填 URL”，它增加风险而没有必要能力收益。

## 11. 撤销、升级和账号切换

### 撤销

- 管理员停用 Profile 后，Profile refresh 返回明确的 `PROFILE_REVOKED`；
- Plugin 删除该 Profile 的 Access/Refresh Token 和 Skill 缓存；
- Gateway 同时撤销 Refresh Token；
- 已创建的远端 Job 不删除，但必须重新授权才能继续读取；
- 本地 Package 与无密 checkpoint 保留。

### 升级

- Plugin 二进制和 Skill 代码仍按官方 Marketplace 升级；
- Profile 使用独立 `schema_version` 和 `revision`；
- Gateway contract 使用独立版本；
- Plugin 在升级前检查当前 Profile 所需能力是否仍支持；
- 不通过长期兼容旧字段解决迁移，提供一次性转换后删除旧形态。

### 换号

Plugin 提供可见的“切换租户账号”：显示当前租户品牌和脱敏账号，调用 revoke，清除该 Profile 的凭据，重新打开同一租户 Gateway 授权。不能要求用户输入命令或手工删除凭据。

## 12. 威胁模型

| 风险 | 主要控制 |
| --- | --- |
| 安装链接被转发 | 链接不含秘密；实际权限由用户 OAuth 决定 |
| Profile 被篡改 | 非对称签名、有效期、revision、固定信任根 |
| Skill 注入恶意 Origin | Skill 无网络配置权；hash 绑定；固定代码执行 |
| Token 发往错误 host | 单一签名 Origin；禁跨 Origin 授权重定向 |
| 租户之间凭据串槽 | profile_id + environment 分区系统凭据库 |
| Gateway 冒充其他 tenant | Token audience、identity tenant_id 与 Profile 交叉校验 |
| Clone URL 窃取 Git 凭据 | git_origins allowlist、HTTPS、无 URL 内嵌凭据、临时 AskPass |
| 写请求网络重试重复计费 | 稳定 Idempotency-Key；只恢复原 Job |
| 租户 Gateway 返回平台全量模型 | Gateway 按内部 subject 过滤；合同验收双用户 |
| Profile 长期离线继续可用 | 短有效期、有限宽限、撤销检查 |
| 官方更新被租户替换 | 更新来源不纳入 Profile 或 Skill |

## 13. 迁移方案

不建议长期兼容现有 `tenant_access_token_v1`。实施顺序：

1. 冻结本文接口审计作为 Consumer 清单；
2. Core 实现 Profile 配置、签名、安装说明和撤销；
3. 定义并发布 Gateway contract 与测试套件；
4. Plugin 实现 Profile 注册、校验、凭据分区和能力路由；
5. 租户 Gateway 完成 OAuth、模型和最小 Compiler 流程；
6. 再按需启用 Forge、Drive Agent 文件、Build/Eval/Release 和反馈；
7. 对已有 `tenant_access_token_v1` 做一次性删除或重新注册，不做静默迁移；
8. 验收后移除旧字段、旧测试和旧文档，避免双协议技术债。

第一版不要同时支持任意端点、任意认证插件、远程可执行 Skill、多 Marketplace 和租户自定义更新源。这些都不是达成白标租户接入所必需。

## 14. 验收清单

### 安装与品牌

- 专属链接不含凭据且只能解析到一个签名 Profile；
- Plugin 只显示租户环境，不出现 Agentour 测试服/正式服选择；
- 租户名称、支持地址和可选 Skill 正确；
- 官方 Plugin 更新来源没有变化。

### 身份与隔离

- 两个内部用户登录后得到不同 subject 和权限数据；
- 换号会撤销旧 Refresh Token，旧账号不能继续调用；
- 同一机器注册两个租户，凭据、Profile、Skill、checkpoint 不串用；
- testing / production 物理分槽；
- 浏览器授权拒绝、state/nonce 错误、Audience 错误、Scope 不足均 fail closed。

### 能力

- 模型列表与逐模型 probe 都只返回用户获授模型；
- Agent、Compiler Task、Package checkpoint 和修复任务按用户隔离；
- Repository、PR、Build、Eval、Release 保持 Commit 和 Job 幂等关系；
- Git credential 只对允许 host、短时、有限次数有效；
- Drive Collection 和参考文件只写入正确 Agent；
- 反馈进入租户配置的支持链路且保持脱敏。

### 故障与撤销

- Profile 过期、撤销、签名错误、Gateway Origin 变化均停止请求；
- GET 短暂失败可重试，POST 不盲目重放；
- 网络中断后恢复同一 Build/Eval/Release；
- Gateway 502/503/504、非法 JSON、超大响应和重定向都有稳定错误；
- 撤销后本地无 Token，Package 与无密 checkpoint 可保留。

### 日志

- 每次请求可关联 request_id / correlation_id；
- 日志、报错、flight recorder、Package、checkpoint、Skill 均无 Token、Cookie、Git credential 或租户长期凭据；
- 慢请求记录总耗时和 Gateway/Core/Drive/Forge 阶段耗时，能判断瓶颈位置。

## 15. 完成标准与实施清单

| 条款 | 设计决策 | 预计实现位置 | 验证 | 当前状态 |
| --- | --- | --- | --- | --- |
| 租户专属安装 | 不含秘密的 Profile ID 链接 | Core 租户管理空间、Plugin enroll 脚本 | 转发链接与首次授权测试 | 设计完成 |
| 可信配置 | Core 非对称签名 Profile | Core Profile API、Plugin verifier | 篡改/过期/撤销测试 | 未实现 |
| 白标环境 | Profile 只声明租户环境 | Plugin bootstrap/UI 指令 | 输出快照与浏览器授权 | 未实现 |
| 自定义身份 | Gateway 暴露固定 OAuth+PKCE | 租户 Gateway | 双账号、换号、撤销 | 未实现 |
| 全接口覆盖 | 本文 4.2-4.9 为基线 | Gateway contract / API client | route-method 合同测试 | 审计完成 |
| 凭据隔离 | profile + environment 分槽 | credential_store.py | 双租户并行测试 | 未实现 |
| 可选 Skill | Profile URL + hash，不控制安全 | Core Skill 发布、Plugin loader | 篡改与越权负测 | 设计完成 |
| Forge Git | 签名 git_origins allowlist | Profile verifier、Git client | 错 host / 过期凭据测试 | 未实现 |
| 更新 | 官方 Marketplace 保持权威 | 现有 update client | Profile 不可覆盖更新源 | 已有基础能力 |
| 去历史兼容 | 删除 tenant_access_token_v1 | API/OAuth/credential tests | 仓库全文扫描 | 未实现 |

只有签名、身份、路由、隔离、真实双用户和跨服务流程全部通过，才能称为 `release-ready`。仅能下载 Skill、打开租户授权页或替换模型列表仍只是 prototype。

## 16. 过度设计与技术债检查

实施时持续使用以下判断：

- 能否用一个 Gateway Origin 解决，就不增加逐端点 URL 配置；
- 能否用 OAuth/OIDC 标准解决，就不下载租户认证代码；
- Skill 只在确有租户说明时创建，不做必选层；
- 未声明的能力不注册、不展示，不用占位 API；
- 不为旧 `tenant_access_token_v1` 建永久兼容分支；
- 不复制整套 API client，只增加 Profile 解析后的 transport context；
- 不在 Plugin 实现 Core / Drive / Forge 服务端状态机；
- 不把“未来也许需要”的多 Gateway、任意脚本和自定义更新源放进第一版；
- 重复的 endpoint、scope、capability 定义应由一份机器可读合同生成或校验，避免文档与代码漂移；
- 每个新增字段必须有明确消费者、撤销方式和测试，否则不加入 Profile。

最终形态的灵活性来自“租户可以在 Gateway 后实现任何内部系统”，而不是让 Plugin 在用户电脑上执行任意租户配置。这样既满足白标和内部权限，又保持一套可升级、可审计的官方 Plugin。
