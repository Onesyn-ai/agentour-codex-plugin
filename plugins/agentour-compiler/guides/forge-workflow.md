# Managed Forge workflow

The current Plugin consumes only the frozen Core developer routes that already exist:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  repository <repository-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-revision <repository-id> <full-commit-sha>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-revision-status <source-revision-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-build <source-revision-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-eval <source-revision-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release --package-id <package-id> --version <semver> \
  --source-revision-id <source-revision-id> --visibility <private|public>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-status <release-id>
```

Every create command derives a stable `Idempotency-Key` from its immutable identifiers. Release sends
only `package_id`, `version`, `source_revision_id`, visibility, and an optional tag. Core remains the
authority for Commit/tree/source, Build, Eval, Artifact, and gate lineage.

Persist interruption state with:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" save-forge-checkpoint \
  --repository-id <repository-id> --commit-sha <full-commit-sha> \
  --remote-job-id <job-id> --stage <stage>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" restore-forge-checkpoint \
  --current-commit-sha <full-commit-sha>
```

The checkpoint schema is closed: `repository_id`, `commit_sha`, `remote_job_id`, `contract_version`,
and `stage`. Unknown fields and credential-like values are rejected. If the current Commit differs,
the restored state clears `remote_job_id` and returns stage `commit_changed` so an old Build/Eval/Release
Job cannot be reused for new source.

## External blockers

Do not invent endpoints or fall back to long-lived Forgejo credentials. The complete Compiler workflow
remains blocked until Core freezes and implements all of the following:

- Repository resolution and idempotent creation for the authenticated developer/Tenant scope, returning
  a Core opaque Repository ID, state, default branch, and contract version.
- The specified `POST /v1/forge/git-credentials` exchange for HTTPS Git. It must derive the actor from
  authentication, accept a Core Repository ID plus `read|write`, and return only a repository-scoped,
  short-lived credential with clone URL, expiry, and authorization epoch. It must never return an
  instance-wide Forgejo token.
- A frozen developer contract for creating a branch/ChangeSet/PR or an explicitly supported Git push
  flow, plus a read contract for current PR/Review/branch-protection state. Navigation URLs alone are
  insufficient for an automated Compiler workflow.
- Build/Eval status retrieval or a single durable Job resource for polling after submission. The current
  frozen routes create Build/Eval records but expose no corresponding GET route.
- Persistent `Idempotency-Key` replay for Source Revision, Build, Eval, and Release creation. Repeating
  the same key and body must return the original resource without duplicate work or billing; reusing a
  key with a different body must return a stable conflict. Client headers alone are not sufficient.

Until these exist, the Plugin may create Source/Build/Eval/Release records only for an already managed
Repository and known pushed Commit. It must not claim that local files, an unpushed Commit, or a merely
submitted Job are published.
