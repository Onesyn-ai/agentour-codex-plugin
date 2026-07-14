# Berth Compiler for Codex

Official Codex plugin for building and migrating agents to the Berth Agent Platform.

It supports two workflows:

- Create a Berth Agent from an idea or specification.
- Convert an existing Agent project into a Berth Package and produce a fidelity report.

The plugin discovers models from the configured Berth platform, creates package files, adds user-readable runtime activity labels, validates approval behavior, runs package checks, and publishes through the developer API.

## Install from the repository marketplace

```bash
codex plugin marketplace add zhaomaota97/berth-codex-plugin
codex plugin add berth-compiler@berth-platform
```

Start a new Codex thread after installation so the skills are loaded.

## Configuration

Set the target platform URL. It is intentionally required; the plugin never silently falls back to localhost.

```bash
export BERTH_URL="https://berth.example.com"
```

Publishing also requires a developer token from the Berth console:

```bash
export BERTH_TOKEN="bt_xxx"
```

Do not commit either value to a repository.

## Usage

Ask Codex one of the following:

```text
Use berth-compiler to create a new Agent.
Use berth-compiler to convert the Agent in this repository.
Use berth-validator to validate packages/my-agent.
```

For an existing Agent, the plugin creates:

```text
.berth/
├── conversion-inventory.json
├── conversion-map.json
└── fidelity-report.json
```

The report distinguishes preserved, adapted, degraded, unsupported, and explicitly removed capabilities. A successful build is not treated as proof of behavioral equivalence.

## Platform endpoints

- Models: `GET ${BERTH_URL}/v1/models`
- Synchronous publish: `POST ${BERTH_URL}/v1/dev/publish`
- Asynchronous publish: `POST ${BERTH_URL}/v1/dev/publish-async`
- Publish job: `GET ${BERTH_URL}/v1/dev/publish-jobs/{job_id}`

## Marketplace support

Codex supports personal and repository/team marketplaces. This repository is a marketplace named `berth-platform`; the plugin is installed as `berth-compiler@berth-platform`.

## Development

```bash
python3 scripts/validate_all.py
```

The repository is licensed under MIT.
