---
name: agentour-validator
description: Internal Agentour Compiler validation stage for Package structure, runtime UX, approvals, secrets, tests, publishing readiness, and migration fidelity. Use as part of agentour-compiler; automatically repair and rerun checks rather than asking users to invoke this skill directly.
---

# Agentour Validator

Validate structure, manifest, unresolved template placeholders, lockfile, runtime entrypoint, model route, user-readable status, approvals, secrets, Smoke Tests, build, and source tests. Cross-check every runtime environment variable against `manifest.secrets`, every `expect_tool` against actual payload source, and every schema-v1 Smoke input for self-contained context. Reject assumed Agentour image/audio/video endpoints unless the current Compiler Contract explicitly advertises them. For conversions, verify inventory, mapping, same-case comparisons, Package hash, and critical assertions. Fix Critical findings before publication without weakening valid tests.
