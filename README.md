# Claude Proxy

Lightweight proxy that translates Anthropic API requests to OpenCode AI's API endpoint.

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

Edit `.env` with your API keys:

```
OPENCODE_BASE=https://opencode.ai/zen/v1
MODEL_NAME=minimax-m2.5-free
PROXY_PORT=8080
PROXY_HOST=0.0.0.0
```

## Run

```bash
./venv/bin/python proxy.py
```

Server runs on `http://localhost:8080` by default.

## Test

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "test", "max_tokens": 50, "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}'
```

## Endpoints

- `POST /v1/messages` - standard Anthropic messages API
- `POST /v1/messages_beta` - beta messages API

Both proxy requests to OpenCode AI and return responses in Anthropic-compatible format.

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

Run proxy before opening Claude in VSCode:

```bash
./venv/bin/python proxy.py
```

## Models (Free)

- `minimax-m2.5-free`
- `big-pickle`
- `ling-2.6-flash-free`
- `hy3-preview-free`
- `nemotron-3-super-free`