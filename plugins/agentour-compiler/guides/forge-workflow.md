# Managed Forge workflow

The current Plugin consumes only the frozen Core developer routes that already exist:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  repositories
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  repository-create --kind agent --name <canonical-name> \
  --default-branch main --visibility private
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  repository-status <repository-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  repository <repository-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  agent-source-prepare <workspace> --agent-id <agent-id> --name <canonical-name> \
  [--repository-id <repository-id>] [--ref <commit-or-tag-or-branch>] [--use-local]
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  agent-source-push <workspace> --agent-id <agent-id> --repository-id <repository-id> \
  --path <package-path> --message <commit-message> [--branch <branch>]
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  git-clone <repository-id> <destination> --commit-sha <full-commit-sha>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  git-push <repository-id> <workspace> --commit-sha <full-commit-sha> --branch <branch>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  change-set-create <repository-id> --head-ref <branch> \
  --expected-head-commit-sha <full-head-commit-sha> --title <title> [--body <body>]
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  pull-request-status <repository-id> <positive-pr-number>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  pull-request-merge <repository-id> <positive-pr-number> \
  --expected-head-commit-sha <full-head-commit-sha> [--required-approvals <count>]
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-revision <repository-id> <full-commit-sha> --pull-request-number <positive-pr-number>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-revision-status <source-revision-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-build <source-revision-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-build-status <build-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-eval <source-revision-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  source-eval-status <eval-run-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  agent-release --agent-id <agent-id> --version <semver> --source-ref <source-ref> \
  --source-revision-id <source-revision-id> --source-commit-sha <full-commit-sha> \
  --pull-request-number <number> --required-approvals <count> --release-notes <text>
```

Source Revision creation always sends both the exact merged Commit and its positive pull request number;
the Plugin never creates a review-free Source Revision from a branch head or standalone Commit. Every
create command derives a stable `Idempotency-Key` from its immutable identifiers. Status commands
read the existing Build/Eval resource and never submit replacement work. `agent-release` is the only
publication command: Core remains the authority for Commit/tree/source, Review, Drive Snapshot,
immutable Tag, Forge Release, Agent Version, and all gate lineage.

`change-set-create` asks Core to create or idempotently recover the PR for one exact pushed head.
`pull-request-status` reads current Provider PR and Review facts; it does not create or alter a Review.
`pull-request-merge` binds the same exact head and lets Core/Forge enforce branch policy and the requested
approval count. Its `--required-approvals` default is `0`; this never turns the author into a reviewer or
fabricates an approval. When policy requires independent Review, obtain that Review through the platform
workflow, reread the PR facts, and pass the required count. Use only the merge Commit returned by Core for
the subsequent Source Revision; never infer it from the branch head.

The Git wrappers are the sole exception to deterministic replay: Core deliberately never replays a
plaintext Git credential. Each wrapper invocation uses one fresh Idempotency-Key, keeps the credential
only in the Git subprocess environment, uses a credential-free HTTPS clone URL, and never writes the
secret to the Package, checkpoint, flight recorder, command line, or any Git remote URL. `git clone`
may create the normal credential-free `origin` remote.

`agent-source-prepare` is the normal entry for creating or updating Agent source. It resolves only
Repositories owned by the authenticated Core identity, creates a personal Repository when no exact
owned canonical-name match exists, and persists only the immutable Agent/Repository binding in
`.agentour/source.json`. It never guesses from a folder name. Explicit refs win; otherwise a proven
local binding wins, followed by the latest default branch. Clean workspaces may fast-forward. Dirty,
locally-ahead, and diverged workspaces are preserved and require an explicit safe resolution.
`agent-source-push` accepts exactly one explicit `--path` pointing to a validated Package directory.
The Package directory must be inside the prepared Source workspace (for example `package/`); copy the
validated Package there and pass that workspace-relative path. It must not be the Source workspace root
or an external Compiler workspace.
It snapshots that Package, clears only the Git index, and projects the Package bytes to Repository
root, so `agentour.json` is always at root. The resulting Commit tree must exactly equal the Package
file list: legacy `packages/<agent-id>/...`, `.agentour`, Compiler specs, flight records, and unrelated
workspace files cannot enter the Commit. Existing staged changes, tracked Compiler state, symlinks,
or tracked changes outside the selected Package fail closed. Removed tracked paths are recorded in the
redacted flight event. It pushes the exact Commit through a fresh short-lived credential. The Plugin
never creates the platform release Tag or Forge Release directly.

Unified release uses a deterministic `Idempotency-Key` derived from immutable inputs. Retry the same
command after interruption; Core serializes that operation and returns the retained completed or failed
evidence. The Plugin never creates or transitions a legacy Release record directly.

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

## Stable contract failures

The client preserves Core's structured `error_code`, `request_id`, `correlation_id`, `stage`,
`target_service`, and `retryable` fields. Important fail-closed codes include:

- Repository/Git: `AUTH_REQUIRED`, `IDEMPOTENCY_KEY_REQUIRED`,
  `IDEMPOTENCY_KEY_PAYLOAD_MISMATCH`, `REPOSITORY_REQUEST_INVALID`,
  `REPOSITORY_NOT_FOUND`, `GIT_CREDENTIAL_REQUEST_INVALID`,
  `GIT_CREDENTIAL_AUTHORIZATION_DENIED`, `GIT_CREDENTIAL_IDEMPOTENCY_REPLAYED`, and
  `GIT_CREDENTIAL_FORGE_ORIGIN_NOT_CONFIGURED`.
- Source/Build: `SOURCE_REVISION_INVALID`, `SOURCE_REVISION_NOT_FOUND`,
  `SOURCE_REVISION_LINEAGE_MISMATCH`, `SOURCE_BUNDLE_DIGEST_MISMATCH`,
  `E2B_BUILD_UNAVAILABLE`, `E2B_BUILD_FAILED`, `E2B_BUILD_LINEAGE_MISMATCH`, and Drive
  workspace/staging/claim errors returned by Core.
- Eval/Release: `EVALUATION_PROFILE_UNSUPPORTED`, `EVAL_BUILD_NOT_READY`,
  `EVAL_BUILD_LINEAGE_MISMATCH`, `EVAL_BUILD_LINEAGE_INCOMPLETE`, `E2B_EVAL_UNAVAILABLE`,
  `EVAL_GATES_FAILED`, `RELEASE_BUILD_NOT_READY`, `RELEASE_EVAL_NOT_READY`, the
  `RELEASE_*_LINEAGE_*` family, `RELEASE_ACTION_NOT_ALLOWED`,
  `RELEASE_AUTHOR_APPROVAL_FORBIDDEN`, `AUTH_REAUTH_REQUIRED`, and the Release Drive/E2B/Registry
  activation or rollback failures.

Never turn a non-retryable contract failure into a blind retry. A transport interruption after a
Source/Build/Eval/Release submission resumes the same resource. A Git credential response interruption
cannot replay plaintext; do not log the response or guess a credential.

## PR and Review boundary

Do not invent endpoints or fall back to long-lived Forgejo credentials. Contract `1.0` exposes
ChangeSet/PR creation, PR/Review fact reads, and policy-checked merge through the Core developer API.
The Plugin never submits a Review, never performs author self-review, and never treats a navigation URL,
an open PR, or a requested approval count as approval evidence. A rejected merge remains `review_pending`;
report the structured Core/Forge error and preserve the same Repository, PR number, head Commit and
checkpoint for recovery. Local or unpushed files, a missing Source Revision, a submitted Job, or a
non-active Release are never described as published.
