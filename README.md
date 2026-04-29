# Claude Proxy

A self-configuring API proxy that translates Anthropic-format requests to a configurable upstream AI provider. The proxy dynamically discovers available models from the provider's documentation and routes each request to the correct upstream format — Anthropic Messages, OpenAI Chat Completions, or OpenAI Responses.

## Features

- **Dynamic model discovery** — fetches the model catalog from OpenCode docs at startup and caches it
- **Smart routing** — routes each model to its correct upstream API family automatically
- **Model fallback** — when `FREE_ONLY=true`, unknown or paid models fall back to a configured free model
- **Streaming relay** — forwards SSE streams, converting them to Anthropic's server-sent event format for all three upstream families
- **TTL-based caches** — avoids hammering OpenCode on every request; caches docs HTML, parsed catalog, live models, and routing table independently
- **Request normalization** — accepts a broad range of input formats (string content, mixed block lists) and normalizes them before transformation

## Project Structure

```
claude-proxy/
├── proxy.py              FastAPI app entrypoint
├── routers/
│   └── proxy.py          Route handlers (/v1/messages, /healthz, /debug/catalog, …)
└── src/
    ├── adapters.py       Response adapters (non-streaming): upstream → Anthropic
    ├── catalog.py        Fetches and caches docs HTML and live /v1/models
    ├── config.py         Environment configuration and shared state
    ├── errors.py          ProxyValidationError and structured error responses
    ├── normalizers.py     Request normalizers: raw body → normalized internal form
    ├── parsing.py         BeautifulSoup HTML catalog parser
    ├── routing.py         Routing table builder and model resolver with fallback
    ├── sse.py             SSE stream adapters: OpenAI → Anthropic event format
    ├── transformers.py    Request builders: normalized → upstream family format
    ├── upstream.py        HTTP client helpers for upstream API calls
    └── utils.py           Shared utilities (ID normalization, JSON helpers, …)
```

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
DOCS_CACHE_TTL=900              # how long to cache docs HTML and parsed catalog
MODELS_CACHE_TTL=300            # how long to cache live model list
ROUTING_CACHE_TTL=300           # how long to cache routing table

# Timeouts (seconds)
REQUEST_TIMEOUT=300
CATALOG_TIMEOUT=30

# Passthrough
PASS_THROUGH_ANTHROPIC_EXTRAS=metadata  # top-level body fields forwarded unchanged

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
    "model": "big-pickle",
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

## Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT REQUEST                                 │
│                   POST /v1/messages { model, messages, ... }              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. RESOLVE MODEL                                                             │
│    • Apply MODEL_NAME default                                               │
│    • Strip "opencode/" prefix if present                                    │
│    • Resolve via routing table                                             │
│    • Fallback to FREE_FALLBACK_MODEL if ALLOW_MODEL_FALLBACK + FREE_ONLY    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
              ┌─────────────────────┬┴─────────────────────┐
              ▼                     ▼                     ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │ anthropic_messages│  │   openai_chat    │  │ openai_responses │
    │    (forward)     │  │    (transform)  │  │   (transform)    │
    └──────────────────┘  └──────────────────┘  └──────────────────┘
              │                     │                     │
              ▼                     ▼                     ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │   /messages      │  │  /chat/completions│ │   /responses    │
    └──────────────────┘  └──────────────────┘  └──────────────────┘
              │                     │                     │
              ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. NORMALIZE + TRANSFORM                                                    │
│    • Parse content blocks (text, tool_use, tool_result)                    │
│    • Normalize messages, tools, system prompt                              │
│    • Transform to upstream family format                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. UPSTREAM (OpenCode AI)                                                  │
│                                                                             │
│    ┌─────────┐  ┌─────────────┐  ┌─────────────┐                         │
│    │ /messages│  │/chat/completions│ │ /responses │                         │
│    └─────────┘  └─────────────┘  └─────────────┘                         │
└─────────────────────────────────────────────────────────────────────────────┘
               │                   │                   │
               ▼ (non-stream)      ▼ (stream)          ▼ (stream)
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │adapters.*       │  │   sse.py relay   │  │  sse.py relay    │
    │_nonstream()      │  │_openai_chat_*    │  │_openai_resp_*    │
    └──────────────────┘  └──────────────────┘  └──────────────────┘
               │                   │                   │
               ▼                   ▼                   ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │adapts to         │  │converts OpenAI   │  │converts OpenAI   │
    │Anthropic message │  │SSE → Anthropic   │  │SSE → Anthropic   │
    │response format  │  │SSE event format  │  │SSE event format  │
    └──────────────────┘  └──────────────────┘  └──────────────────┘
               │                   │                   │
               └───────────────────┴───────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. RESPONSE TO CLIENT                                                       │
│    • Non-stream: JSON with Anthropic message format                         │
│    • Stream: SSE with message_start, content_block_*, message_delta, etc.    │
│    • Header X-Proxy-Model-Fallback: {requested}→{effective} if fallback used │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Caching Layers

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TTL-based Caches (independent, each has its own TTL)                           │
│                                                                             │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                │
│   │  docs_html  │    │   models    │    │   routing  │                │
│   │  (DOCS_     │    │  (MODELS_   │    │ (ROUTING_   │                │
│   │  CACHE_TTL) │    │  CACHE_TTL) │    │ CACHE_TTL) │                │
│   └──────────────┘    └──────────────┘    └──────────────┘                │
│         │                    │                    │                               │
│         ▼                    ▼                    ▼                               │
│   Raw docs HTML    →   Live model list  →  Routing table                      │
│   (BeautifulSoup)      /v1/models     model_id → family/path               │
└─────────────────────────────────────────────────────────────────────────────┘
```

## How Routing Works

On startup (and periodically via cache TTL), the proxy:

1. Fetches OpenCode's docs page and parses model/endpoint/pricing tables
2. Fetches the live `/v1/models` catalog from the upstream API
3. Builds a routing table: `model_id → { family, endpoint, path, free, live }`

When a request arrives:

1. Resolves the requested model (applies `MODEL_NAME` default; strips `opencode/` prefix)
2. Looks up the model in the routing table
3. If not found and `ALLOW_MODEL_FALLBACK=true` + `FREE_ONLY=true`, falls back to `FREE_FALLBACK_MODEL`
4. Normalizes the request body (content blocks, tools, system prompt)
5. Transforms the normalized request to the upstream format:
   - `anthropic_messages` — forwarded directly
   - `openai_chat` — converts messages, tools, and stream options to OpenAI Chat format
   - `openai_responses` — converts messages to OpenAI Responses format
6. For streaming responses, relays and converts SSE events to Anthropic's SSE format

When a fallback model is used, the response includes the header `X-Proxy-Model-Fallback: {requested}->{effective}`.

## Health & Debug

```bash
# Check proxy status
curl http://localhost:8080/healthz

# List all routable models
curl http://localhost:8080/v1/models

# Force-refresh and dump full catalog
curl http://localhost:8080/debug/catalog
```
