# Claude Proxy

A self-configuring API proxy that translates Anthropic-format requests to OpenCode AI's API. The proxy dynamically discovers available models from OpenCode's documentation and routes requests to the correct upstream format (Anthropic Messages, OpenAI Chat Completions, or OpenAI Responses).

## Features

- **Dynamic model discovery** — pulls the model catalog from OpenCode docs at startup and caches it
- **Smart routing** — routes each model to its correct upstream API family automatically
- **Model fallback** — when `FREE_ONLY=true`, unknown/paid models fall back to a configured free model
- **Streaming relay** — forwards SSE streams, converting them to Anthropic's event format
- **TTL-based caches** — avoids hammering OpenCode on every request; caches docs HTML, parsed catalog, live models, and routing table separately

## Setup

```bash
# Create virtual environment
uv venv venv --python python3.13
uv pip install -r requirements.txt -p venv

# Or with pip
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy env file
cp .env.example .env
```

Edit `.env` with your settings.

## Configuration

```env
# Proxy server
PROXY_HOST=0.0.0.0
PROXY_PORT=8080

# Upstream
OPENCODE_BASE=https://opencode.ai/zen/v1
DOCS_URL=https://opencode.ai/docs/zen/

# Model selection
MODEL_NAME=big-pickle           # default model when client sends none
FREE_ONLY=true                  # only route models marked as free in docs
REQUIRE_LIVE_MODEL=true         # skip models not in live /v1/models catalog
FREE_FALLBACK_MODEL=big-pickle  # fallback model when FREE_ONLY=true and requested model is unavailable
ALLOW_MODEL_FALLBACK=true       # enable automatic fallback

# Caching (seconds)
DOCS_CACHE_TTL=900              # how long to cache docs HTML
MODELS_CACHE_TTL=300            # how long to cache live model list
ROUTING_CACHE_TTL=300           # how long to cache routing table

# Timeouts (seconds)
REQUEST_TIMEOUT=300
CATALOG_TIMEOUT=30

# Passthrough
PASS_THROUGH_ANTHROPIC_EXTRAS=metadata

LOG_LEVEL=INFO
```

## Run

```bash
./venv/bin/python proxy.py
```

Server starts on `http://0.0.0.0:8080` by default.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/messages` | Anthropic Messages API (proxied) |
| `POST` | `/v1/messages_beta` | Beta Messages API (proxied) |
| `GET` | `/v1/models` | Lists all currently routable models |
| `GET` | `/healthz` | Proxy health and routing stats |
| `GET` | `/debug/catalog` | Full catalog debug info (forces cache refresh) |

## Test

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "test",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
  }'
```

## Claude Code (VSCode Extension)

Add to VSCode settings (`~/.vscode/settings.json`):

```json
{
  "claudeCode.environmentVariables": [
    {
      "name": "ANTHROPIC_BASE_URL",
      "value": "http://localhost:8080"
    },
    {
      "name": "ANTHROPIC_API_KEY",
      "value": "dummy-key"
    }
  ]
}
```

Reload VSCode: Ctrl+Shift+P → "Reload Window"

## How Routing Works

On startup (and periodically via cache TTL), the proxy:

1. Fetches OpenCode's docs page and parses model/endpoint tables
2. Fetches the live `/v1/models` catalog from the upstream API
3. Builds a routing table: `model_id → { family, endpoint, free, live }`

When a request arrives:

1. Resolves the requested model (applies `MODEL_NAME` default)
2. Looks up the model in the routing table
3. If not found and `ALLOW_MODEL_FALLBACK=true` + `FREE_ONLY=true`, falls back to `FREE_FALLBACK_MODEL`
4. Transforms the request body to the upstream format:
   - `anthropic_messages` family — forward directly
   - `openai_chat` family — convert messages and tools to OpenAI Chat format
   - `openai_responses` family — convert messages to OpenAI Responses format
5. For streaming responses, relays and converts SSE events to Anthropic's SSE format

## Health & Debug

```bash
# Check proxy status
curl http://localhost:8080/healthz

# List all routable models
curl http://localhost:8080/v1/models

# Force-refresh and dump full catalog
curl http://localhost:8080/debug/catalog
```
