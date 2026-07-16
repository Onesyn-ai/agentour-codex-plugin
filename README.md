# Agentour Compiler for Codex

Official Codex Plugin for inventing new Agentour Agents or reconstructing existing Agent projects with high behavioral fidelity.

## Install

```bash
codex plugin marketplace add Onesyn-ai/agentour-codex-plugin
codex plugin add agentour-compiler@agentour-platform
```

Start a new Thread after installation or upgrade.

## Usage

Use natural language; do not choose internal Skills:

```text
帮我创建并上传一个 Agentour Agent。每轮只问我一个问题，其余流程你自动完成。
```

The Plugin enforces this sequence:

1. Choose **本地服** (`http://127.0.0.1:8600`) or **比赛服** (`https://agentour.ai`).
2. Enter a `at_` developer token; the Plugin validates it with `GET /v1/dev/me` and asks again if invalid.
3. Fetch models from that platform's `GET /v1/models`, probe every model, and remove unavailable models before selection.
4. Choose whether to reconstruct an existing Agent or invent a new one.
5. Complete a one-question-per-turn brainstorm and grill-me interview.
6. Generate Package(s), validate, repair, and verify fidelity.
7. Choose private or public upload, then publish and follow the job.

The token is never written to files or reports.

At workflow startup the Plugin checks the latest GitHub Marketplace version. If a newer version exists it runs the Codex Plugin installer automatically, then asks you to start a new Thread so Codex loads the new code.

## Multiple source Agents

If an existing repository contains multiple Agents, the Plugin inventories them first and asks one scope choice: merge them into one Package, convert all into separate Packages, or select a subset. Each converted Package receives its own capability map and fidelity evidence.

## Fidelity

A successful build is not treated as behavioral equivalence. The Plugin compares source and converted workflows, tools, approvals, attachments, schemas, artefacts, edge cases, failures, retries, and multi-turn behavior. Critical mismatches block publication regardless of aggregate score.

## Development

```bash
python3 scripts/validate_all.py
python3 -m unittest tests/test_plugin.py
```

MIT licensed.
