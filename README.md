# Claude Proxy

A self-configuring API proxy that translates Anthropic-format requests to a configurable upstream AI provider. The proxy dynamically discovers available models from the provider's documentation and routes each request to the correct upstream format — Anthropic Messages, OpenAI Chat Completions, or OpenAI Responses.

The proxy is built around a **core + provider plugin architecture**: `core/` contains the socket (never changes between providers), and `providers/` contains the plug (one directory per provider). Adding a new provider requires no changes to core.

## Features

- **Dynamic model discovery** — fetches the model catalog from provider docs at startup and caches it
- **Smart routing** — routes each model to its correct upstream API family automatically
- **Model fallback** — when `FREE_ONLY=true`, unknown or paid models fall back to a configured free model
- **Streaming relay** — forwards SSE streams, converting them to Anthropic's server-sent event format for all three upstream families
- **TTL-based caches** — avoids hammering the provider on every request; caches docs HTML, parsed catalog, live models, and routing table independently
- **Request normalization** — accepts a broad range of input formats (string content, mixed block lists) and normalizes them before transformation

## Project Structure

```
claude-proxy/
├── proxy.py                   FastAPI app entrypoint
├── .env.example              Environment variables template
├── core/                     Provider-agnostic (never changes between providers)
│   ├── interfaces.py         ProviderABC + TypedDict types
│   ├── config.py              Provider-neutral env vars
│   ├── state.py               Shared in-process state (client, routing cache)
│   ├── routing.py             Routing table builder and model resolver
│   ├── upstream.py           HTTP client delegator (uses provider.send_*)
│   ├── normalizers.py        Request normalizers: raw body → normalized form
│   ├── transformers.py       Request builders: normalized → upstream family format
│   ├── adapters.py           Response adapters (non-streaming): upstream → Anthropic
│   ├── sse.py                SSE stream adapters: OpenAI → Anthropic SSE
│   ├── errors.py             ProxyValidationError and structured error responses
│   └── utils.py              Shared utilities
├── providers/               Pluggable provider adapters
│   ├── __init__.py           load_provider(name) — registry and factory
│   ├── registry.py            get_provider() — runtime singleton
│   └── opencode/             OpenCode provider (current default)
│       ├── config.py          Provider-specific env vars (OPENCODE_*, DOCS_URL)
│       ├── catalog.py        Fetches docs HTML and live /v1/models with cache
│       ├── parsing.py        BeautifulSoup HTML catalog parser
│       ├── upstream.py       HTTP client for OpenCode API
│       └── provider.py       OpenCodeProvider(ProviderABC) — the plugin
└── routers/
    └── proxy.py               Route handlers
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
# ── Provider ────────────────────────────────────────────────────────────────
PROVIDER_NAME=opencode            # which provider to load

# OpenCode provider (used when PROVIDER_NAME=opencode)
OPENCODE_BASE=https://opencode.ai/zen/v1
DOCS_URL=https://opencode.ai/docs/zen/

# ── Model selection ────────────────────────────────────────────────────────
MODEL_NAME=minimax-m2.5-free    # default model when client sends none
FREE_ONLY=true                  # only route models marked as free in docs
REQUIRE_LIVE_MODEL=true          # skip models not in live /v1/models catalog
FREE_FALLBACK_MODEL=minimax-m2.5-free  # fallback when FREE_ONLY=true
ALLOW_MODEL_FALLBACK=true       # enable automatic fallback

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────
ROUTING_CACHE_TTL=300

# ── Timeouts (seconds) ───────────────────────────────────────────────────
REQUEST_TIMEOUT=300
CATALOG_TIMEOUT=30

# ── Passthrough ────────────────────────────────────────────────────────────
PASS_THROUGH_ANTHROPIC_EXTRAS=metadata  # top-level body fields forwarded unchanged

# ── Server ────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO
PROXY_HOST=0.0.0.0
PROXY_PORT=8080
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
    "model": "minimax-m2.5-free",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
  }'
```

## Adding a New Provider

Adding a second provider (e.g. `providers/another/`) requires only:

1. Create `providers/another/` directory
2. Implement `ProviderABC` — see `core/interfaces.py`
3. Register in `providers/__init__.py` with `@register_provider("another")`
4. Set `PROVIDER_NAME=another` in `.env`

Core is not touched. Routers are not touched.

## Workflow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT REQUEST                                 │
│                   POST /v1/messages { model, messages, ... }                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. GET PROVIDER (via get_provider() singleton)                              │
│    Provider injected at startup — never fetched at request time             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. RESOLVE MODEL                                                            │
│    • Apply provider.default_model as default                                │
│    • Call provider.normalize_model_id() (strips prefixes)                   │
│    • Look up in routing table                                               │
│    • Fall back to provider.default_fallback_model if                        │
│      ALLOW_MODEL_FALLBACK + FREE_ONLY                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
              ┌─────────────────────┬┴─────────────────────┐
              ▼                     ▼                     ▼
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │anthropic_messages│  │   openai_chat    │  │ openai_responses │
    │    (forward)     │  │    (transform)   │  │   (transform)    │
    └──────────────────┘  └──────────────────┘  └──────────────────┘
              │                     │                     │
              ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. NORMALIZE + TRANSFORM                                                    │
│    • Parse content blocks (text, tool_use, tool_result)                     │
│    • Normalize messages, tools, system prompt                               │
│    • Transform to upstream family format                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. UPSTREAM (via provider.send_json / provider.send_stream)                 │
│    provider.send_json(path, payload, httpx_client, timeout)                 │
│    URL constructed internally by the provider                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. ADAPT RESPONSE (provider-agnostic — core/adapters.py)                    │
│    • Non-stream: upstream response → Anthropic message                      │
│    • Stream: SSE relay via core/sse.py                                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. RESPONSE TO CLIENT                                                       │
│    • Non-stream: JSON with Anthropic message format                         │
│    • Stream: SSE with Anthropic event types                                 │
│    • Header X-Proxy-Model-Fallback if fallback used                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Caching Layers

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TTL-based Caches                                                            │
│                                                                             │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│   │  docs_html       │  │  models_cache    │  │  routing_cache   │          │
│   │  (provider)      │  │  (provider)      │  │  (core.state)    │          │
│   └──────────────────┘  └──────────────────┘  └──────────────────┘          │
│         │                    │                    │                         │
│         ▼                    ▼                    ▼                         │
│   Raw docs HTML        Live /v1/models    Routing table                     │
│   (BeautifulSoup)       (provider)       model_id → family                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Health & Debug

```bash
# Check proxy status
curl http://localhost:8080/healthz

# List all routable models
curl http://localhost:8080/v1/models

# Force-refresh and dump full catalog
curl http://localhost:8080/debug/catalog
```