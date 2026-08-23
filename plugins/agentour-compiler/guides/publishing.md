# Publishing

Use the fixed selected platform URL and discover models through its public API. The Plugin must complete
browser Authorization Code + PKCE before any authenticated operation. Access tokens are refreshed
automatically; rotating refresh credentials live only in the operating-system credential store. If no
system credential store is available, stop and configure one; never write credentials to a plaintext
fallback file. Never ask for a copied Token, browser Cookie, OIDC provider
token, or `localStorage` value.

Publication uses only the `agent-release` transaction documented in `guides/forge-workflow.md` after
source push, Source Revision and required Review facts exist. The legacy direct package upload and
`publish-async` commands are retired because they cannot prove Repository, Drive Snapshot and Tag
lineage.

Before uploading, show the destination host, Agent ID, version, visibility, fidelity grade, and unsupported capabilities. Never publish to a remote platform without authorization.

The credential bundle is the sole identity authority. A platform OAuth bundle uses the platform
account. A `tenant_access_token_v1` bundle uses its bound API origin and short-lived tenant token
without opening platform OAuth or asking the operator to select an identity type. The Plugin never
sends a caller-supplied tenant ID or caller ID. With a tenant credential, `private` means visible only
to the creating internal caller and `public` means tenant-wide visibility; neither setting publishes
the Agent outside the tenant. Global publication is a separate tenant-owner and platform-review flow.

Run `remote-build` only after final confirmation. Cached responses are successful evidence and
do not consume quota. Treat `429` as a quota wait condition, not as a retry invitation. Repair
deterministic structured Gate failures before resubmitting, and use `cancel-build <job-id>` for a
superseded Build.
If local polling or networking is interrupted, recover the same Job with `track-build <job-id>`.
An observation failure is not evidence that the remote Build failed.

On Windows, pass structured Compiler Task state with `--state-file <utf8-json-file>` instead of
embedding JSON in PowerShell arguments. Before paid Build, the static validator must have checked
that Smoke cases are self-contained, expected tools exist, all template placeholders are replaced,
and runtime environment variables match `manifest.secrets`.

For the managed Forge Source Revision/Build/Eval/Release commands and the exact external API blockers,
read `guides/forge-workflow.md`. Never substitute local Git state for a remote Source Revision.

## Plugin release identity

Every release uses a new patch version plus one `+codex.*` cache identity. Before relying on an
installed release, run the repository's `scripts/verify_plugin_release.py verify-cache` command
against the frozen source checkout. It must verify the exact installed manifest and critical-file
SHA-256 values. A missing cache directory, unchanged version with changed source, or any hash mismatch
blocks publishing. Reinstall from the configured `agentour-platform` Marketplace and start a new
Thread only after the verifier succeeds; never copy files directly into the Codex cache.
