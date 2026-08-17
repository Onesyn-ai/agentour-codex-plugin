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
  git-clone <repository-id> <destination> --commit-sha <full-commit-sha>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  git-push <repository-id> <workspace> --commit-sha <full-commit-sha> --branch <branch>
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
  release --package-id <package-id> --version <semver> \
  --source-revision-id <source-revision-id> --visibility <private|public>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-status <release-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-submit-review <release-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-approve <release-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-activate <release-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-withdraw <release-id>
python3 "${CODEX_PLUGIN_ROOT}/scripts/agentour_api.py" --platform <test|production> \
  release-rollback <release-id> [--target-release-id <deprecated-release-id>]
```

Source Revision creation always sends both the exact merged Commit and its positive pull request number;
the Plugin never creates a review-free Source Revision from a branch head or standalone Commit. Every
create command derives a stable `Idempotency-Key` from its immutable identifiers. Status commands
read the existing Build/Eval resource and never submit replacement work. Release sends
only `package_id`, `version`, `source_revision_id`, visibility, and an optional tag. Core remains the
authority for Commit/tree/source, Build, Eval, Artifact, and gate lineage.

The Git wrappers are the sole exception to deterministic replay: Core deliberately never replays a
plaintext Git credential. Each wrapper invocation uses one fresh Idempotency-Key, keeps the credential
only in the Git subprocess environment, uses a credential-free HTTPS clone URL, and never writes the
secret to the Package, checkpoint, flight recorder, command line, or any Git remote URL. `git clone`
may create the normal credential-free `origin` remote.

Release transitions also use deterministic `Idempotency-Key` values. The server derives the actor,
approval policy and current authorization. Rollback may optionally name one immutable deprecated
`target_release_id`; otherwise Core selects the newest valid candidate. The Plugin never supplies an
approver, active version, Drive object reference, or Registry override. Read `release-status` after an
interrupted transition instead of assuming the side effect failed or submitting a different Release.

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

## External blockers

Do not invent endpoints or fall back to long-lived Forgejo credentials. Repository creation, projection
status, short-lived HTTPS clone/push, Source Revision, Build, Eval, Release, and Release transitions are
available in contract `1.0`. The complete Compiler workflow still requires Core to freeze and implement
developer Commit-detail, branch/ChangeSet/PR creation, and PR/Review/branch-protection reads. Navigation
URLs alone are insufficient for automated Review. Until those routes exist, the Plugin may push a named
branch and report `review_pending`, but it must not claim that Review passed or that local/unpushed files,
a missing Source Revision, a submitted Job, or a non-active Release are published.
