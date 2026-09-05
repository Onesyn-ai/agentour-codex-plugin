# Woodpecker quality migration

Status: `prototype` (local validation passed; real Woodpecker run pending).

The new `.woodpecker/quality.yaml` uses the official Git clone plugin and a
Python 3.12/Git image, both mirrored by immutable digest in Onesyn CI. It retains
the existing manifest/release-integrity, contract-test and script-compile commands,
and uses the dedicated light workload slot. No deployment or publishing credential
is introduced, and no Plugin product file is changed.

The GitHub quality workflow remains active until both systems pass against the
same source Commit, including a real Woodpecker checkout and job. Repository
activation, OAuth authorization and Required Check switching remain pending.
This quality-only first job does not claim Candidate admission or release readiness.

Local evidence: manifest/release integrity validation passed, 103 Plugin tests
passed, and the pinned Woodpecker 3.18.0 CLI accepted the configuration with strict
lint and the exact local trusted-clone image.

| Requirement | Decision | Implementation | Verification | Status |
| --- | --- | --- | --- | --- |
| Existing quality behavior | Reuse the same four checks | `.woodpecker/quality.yaml` | Existing checks plus matching real CI runs | pending |
| Official checkout | Pin plugin-git 2.10.0 local digest | clone step | Exact source in real Woodpecker job | pending |
| Resource limits | Use light label and sequential commands | workflow labels | Scheduled on light Agent | pending |
| Preserve GitHub coverage | Keep old workflow until shadow evidence | `.github/workflows/plugin-quality.yml` | Same Commit status comparison | pending |
