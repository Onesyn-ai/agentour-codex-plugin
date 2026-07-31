# Agentour Feishu Runtime capability catalog

Use this catalog whenever an Agent needs to read or change Feishu/Lark resources. Declare only the
minimum necessary Skills. Do not copy Skill content, `lark-cli`, application credentials, or user tokens
into the Package.

## Development-time latest-version gate

Before generating or modifying a Feishu Package, run `scripts/lark_cli_preflight.py --skills ...` with
the exact declared business Skills. The preflight must prove that all three versions match: the local
`lark-cli`, the latest GitHub Release from `larksuite/cli`, and npm latest for `@larksuite/cli`. It must
also successfully run `lark-cli skills list` and `lark-cli skills read <skill>` for every selected Skill.
Failure to reach either official registry, mismatched latest versions, an unsuccessful upgrade, or an
unreadable Skill is a blocking error. Do not generate from stale cached knowledge or guess commands.

This is a Compiler development prerequisite only. The Package must not bundle the CLI or Skills.
Agentour Runtime injects its platform-verified CLI/Skill bundle and scoped user authorization.

## Credential boundary

The developer only declares capabilities. The end user authorizes their Feishu account in Agentour and
grants individual Agents access. Agentour retains the long-lived refresh token in its encrypted
credential store and provides a short-lived user credential plus the selected official Skills to the
isolated Runtime. Agent code calls `lark-cli`; it must not read, print, persist, return, or request the
credential itself.

Packages must not declare or read `FEISHU_APP_ID` or `FEISHU_APP_SECRET`, request an Access Token from
the user, bundle a CLI/Skill copy, or implement a private client against `open.feishu.cn/open-apis`.
Agentour owns application-level credentials and token refresh. Its Runtime may provide non-secret CLI
identity metadata, but that is not a Package Secret contract.

Authenticated Web and SDK launches may use the current Agentour account's authorized Feishu identity
when that Agent is granted access. Feishu-channel launches use the message sender's identity. Anonymous
shares never inherit an owner's Feishu credentials.

## Manifest example

```json
"channel_capabilities": {
  "feishu": {
    "required": true,
    "skills": ["lark-wiki", "lark-doc"]
  }
}
```

## Official Skill catalog

| Skill | Purpose |
|---|---|
| `lark-approval` | Search, inspect, process, and start approvals |
| `lark-apps` | Miaoda/Spark applications, hosting, logs, and automation |
| `lark-attendance` | Personal attendance records |
| `lark-base` | Base tables, fields, records, views, and workflows |
| `lark-calendar` | Calendars, events, attendees, availability, and rooms |
| `lark-contact` | Resolve users and inspect contact information |
| `lark-doc` | Read and edit Feishu documents |
| `lark-drive` | Drive files, folders, upload/download, and permissions |
| `lark-event` | Subscribe to and consume real-time events |
| `lark-im` | Messages, chats, files, cards, and callbacks |
| `lark-mail` | Search, draft, and send mail |
| `lark-markdown` | Create, read, and edit Markdown files |
| `lark-minutes` | Search Minutes and access media/transcript outputs |
| `lark-note` | Read a known meeting Note ID |
| `lark-okr` | Objectives, key results, alignment, and progress |
| `lark-openapi-explorer` | Discover official APIs not covered by another Skill |
| `lark-shared` | Diagnose CLI identity, authorization, and Scope |
| `lark-sheets` | Spreadsheets, formulas, styles, and charts |
| `lark-skill-maker` | Wrap a Feishu API workflow as a reusable Skill |
| `lark-slides` | Create and edit presentations |
| `lark-task` | Tasks, lists, subtasks, assignees, and attachments |
| `lark-vc` | Ended video meetings, attendees, and meeting outputs |
| `lark-vc-agent` | Join a live meeting and consume in-meeting events |
| `lark-whiteboard` | Inspect, export, and edit whiteboards |
| `lark-wiki` | Wiki spaces, members, and nodes |
| `lark-workflow-meeting-summary` | Summarize meetings over a time range |
| `lark-workflow-standup-report` | Combine calendar and incomplete tasks |

Prefer a concrete Skill. Add `lark-openapi-explorer` only when no concrete Skill covers the required
operation. Cross-resource workflows declare every required Skill. `lark-shared` is for authorization
diagnostics, not ordinary token acquisition.
