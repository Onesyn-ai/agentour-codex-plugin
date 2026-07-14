# Publishing

Require explicit `BERTH_URL`; discover models through `${BERTH_URL}/v1/models`. Require `BERTH_TOKEN` only when publishing.

Prefer asynchronous publication:

```bash
python3 "${CODEX_PLUGIN_ROOT}/scripts/berth_api.py" publish-async packages/<agent-id>
```

Before uploading, show the destination host, Agent ID, version, visibility, fidelity grade, and unsupported capabilities. Never publish to a remote platform without authorization.
