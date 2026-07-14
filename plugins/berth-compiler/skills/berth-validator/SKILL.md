---
name: berth-validator
description: Validate a Berth Package for structure, runtime status UX, approval semantics, secrets, smoke coverage, publishing readiness, and migration fidelity evidence. Use for package review before local or remote publication.
---

# Berth Package Validator

Validate without hiding or weakening failures.

1. Run `${CODEX_PLUGIN_ROOT}/scripts/validate_package.py <package-dir>`.
2. Inspect `berth.json`, runtime entrypoint, tools, skills, lockfile, smoke tests, README, and release notes.
3. Ensure every actual capability has a `runtime_ui` entry and no user-facing text exposes internal terms.
4. Ensure `waiting_approval` is described as waiting, never running.
5. Ensure side-effect tools declare approval and explain action, reason, and impact.
6. Search for committed secrets and unsafe localhost fallbacks.
7. Run package build and available tests.
8. For migrations, verify inventory, mapping, critical assertions, Package hash, and fidelity report.

Output Critical, Warnings, Passed, and Unverified sections. Critical findings block publishing.
