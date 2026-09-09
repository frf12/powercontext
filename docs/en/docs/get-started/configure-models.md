---
title: Enable Memory extraction and vector search
description: Configure models, start the Server, and verify the complete Memory loop.
---

# Enable Memory extraction and vector search

These steps use `master` and Bash. Windows support is `experimental`; see [platform requirements](install-and-run.md).

`powercontext server run` works without model configuration, but model-backed extraction and vector search stay off.
`config init` opens a guided setup and asks for storage, usage scenario, and memory capabilities before collecting
the model connections those capabilities need. Basic memory can use explicit Agent-saved entries without a separate
model API; this guide covers automatic extraction and vector search.

| Capability | Minimal Server | Configured runtime |
| --- | --- | --- |
| Source capture | Enabled | Enabled |
| Automatic Memory extraction | Disabled | Enabled |
| Search modes | `auto, fts` | `auto, fts, vector, hybrid` |
| Dashboard | Opt-in, static token required | Opt-in, static token required |
| MCP endpoint | `/mcp` | `/mcp` |

The Server creates one opaque default Scope on first startup. Integrations may bind a Session or workspace to that
default or to another existing Scope.

## 1. Install and configure

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext config init --output .env
```

Select full memory, or select automatic Memory extraction and semantic retrieval individually. The wizard collects
Generation and Embedding models, credentials, the required embedding profile and dimension, and processing
schedules. It generates configuration and follow-up instructions; it does not deploy or test the remote connections.

The first screen offers English and Chinese, with a detected system-language default and English fallback for
unsupported or unknown languages. Use `--language en` or `--language zh` to choose explicitly. For a model-free
template to edit manually, add `--template`. See [Configure a Server environment](configure-server-environment.md)
for language precedence and existing-file behavior.

For manual configuration, the equivalent Memory extraction and vector retrieval settings include the following,
plus the credential and Base URL required by the selected provider:

```dotenv
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:generation-model
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=provider-embedding-model-1536-unit-v1
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1536
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
```

`generation-model` powers automatic extraction and generation, while `embedding-model` powers vector retrieval;
scheduled Source processing also requires a generation model. For a local provider that ignores authentication, use a
non-secret placeholder accepted by that provider.

Inspect and validate the generated file without printing credentials:

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

The generated file contains Server, database, and selected integration settings. The wizard configures schedules
for the automatic capabilities you selected; basic mode does not enable automatic extraction. Scope identity is
owned by the running Server and is not invented by the Config Generator. Validate the actual Memory loop below
after starting the service; successfully saving the file is not an end-to-end check.

## 2. Start and verify the Server

```bash
powercontext server run --env-file .env
```

In another terminal:

```bash
set -a
. ./.env
set +a
export POWERCONTEXT_CLIENT_API_TOKEN="${POWERCONTEXT_CLIENT_API_TOKEN:-${POWERCONTEXT_SERVER_AUTH_TOKEN:-}}"
powercontext doctor
powercontext ready
powercontext capabilities
```

The full runtime is ready when readiness is `ready`, Memory extraction is enabled, and search modes include `vector`
and `hybrid`. If only `auto, fts` appear, check the Embedding model, profile ID, dimension, credential, and Base URL.

The following requests use the local Server address. Adjust it if you selected another host or port. If Dashboard
or authenticated access is enabled, these checks need `POWERCONTEXT_CLIENT_API_TOKEN`. The export above uses the
protected Server token when no client token is already loaded. The generated client environment file provides the
connection settings for Agent processes without exposing model credentials.

Retrieve the default Scope's opaque ID for the following API checks:

```bash
SCOPE_ID="$(curl -fsS http://127.0.0.1:8000/v1/scopes/default \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  | python -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])')"
export SCOPE_ID
```

## 3. Verify the Memory loop

Capture a Source with a unique ID:

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
curl -fsS -X POST http://127.0.0.1:8000/v1/sources/content \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"source_id\":\"${SOURCE_ID}\",\"content\":\"PowerContext quick start check: prefer small, verifiable steps.\"}"
```

Keep the returned `position`, then flush the same Scope:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/flush \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

The returned `current_cursor` must be at least the capture `position`. `status: "idle"` is valid when the Scheduler
already processed the Source.

List Memory entries:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/entries/list \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

Find an entry whose `source_refs` contains the captured Source and record its `citation.entry_id`. Then verify vector
retrieval:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/search \
  -H "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"query\":\"verifiable steps\",\"mode\":\"vector\",\"limit\":50}"
```

The round trip is verified when the response has `mode: "vector"`, the recorded `entry_id`, and `vector` in
`matched_by`. Confirm model usage with:

```bash
powercontext stats --scope-id "$SCOPE_ID"
```

## 4. Connect an Agent

After verifying the Server, follow the [guide for your Agent](../integrations/index.md) to configure its connection, authentication, and capture behavior.

## Data and restart behavior

With no database override, SQLite stores `powercontext.db` under the user data directory:

- Linux: `$XDG_DATA_HOME/powercontext`, or `~/.local/share/powercontext`;
- macOS: `~/Library/Application Support/powercontext`;
- Windows (`experimental`): `%LOCALAPPDATA%\\powercontext`.

Press `Ctrl+C` to stop the Server. Restart it with the same `.env` and data directory. The default Scope and its opaque
ID remain stable because they are persisted in the database.

| Symptom | Action |
| --- | --- |
| Readiness is `degraded` | Check model identifiers, credentials, and Base URLs |
| No `vector` or `hybrid` mode | Configure Embedding model, profile ID, and dimension together |
| Sources remain pending | Enable the Scheduler or call `/v1/memory/flush` |
| Existing data is missing | Restore the previous database URL or `POWERCONTEXT_HOME` |

See [Troubleshooting](../operate/troubleshoot.md) and [Configuration](../operate/configuration.md) for details.

To organize saved Artifacts and individual Memory entries, see [Custom tags](../workflows/manage-artifact-tags.md).
