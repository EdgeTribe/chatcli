# chatcli

A simple terminal chat client for OpenAI-compatible APIs with MCP tool support.

## Features

- Streaming responses with a blinking cursor while waiting
- Connects to any OpenAI-compatible API (Ollama, vLLM, LiteLLM, OpenAI, etc.)
- MCP (Model Context Protocol) tool support via HTTP SSE
- Automatic tool-call loop: the model can call tools and reason over results
- Visual frame around tool calls to distinguish them from model responses

## Installation

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python3 chatcli.py
```

### Configuration

All configuration is done via environment variables:

| Variable | Default | Description |
|---|---|---|
| `CHAT_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible API base URL |
| `CHAT_API_KEY` | (empty) | API key for authentication |
| `CHAT_MODEL` | `llama3` | Model name to use |
| `CHAT_SYSTEM_PROMPT` | `You are a helpful assistant.` | System prompt |
| `CHAT_MCP_SERVERS` | `{}` | JSON object mapping server names to SSE URLs |

### Examples

Chat with a local Ollama instance (default):

```bash
python3 chatcli.py
```

Chat with a remote API:

```bash
CHAT_BASE_URL=https://api.example.com/v1 \
CHAT_API_KEY=sk-your-key \
CHAT_MODEL=gpt-4 \
python3 chatcli.py
```

Chat with MCP tools:

```bash
CHAT_MCP_SERVERS='{"mytools": "http://localhost:3000/sse"}' \
python3 chatcli.py
```

Multiple MCP servers:

```bash
CHAT_MCP_SERVERS='{"search": "http://localhost:3000/sse", "db": "http://localhost:3001/sse"}' \
python3 chatcli.py
```

### Commands

| Command | Description |
|---|---|
| `/quit` | Exit the chat |
| `/clear` | Clear conversation history |
| `/tools` | List all connected MCP tools |

## License

This project is licensed under the GNU General Public License v2.0. See [LICENSE](LICENSE) for details.
