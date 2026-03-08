#!/usr/bin/env python3
"""Simple CLI chat client for OpenAI-compatible servers with MCP tool support."""

import asyncio
import json
import os
import re
import shutil
import sys
import threading
from contextlib import AsyncExitStack

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client

# Configuration via environment variables
API_KEY = os.environ.get("CHAT_API_KEY", "")
BASE_URL = os.environ.get("CHAT_BASE_URL", "http://localhost:11434/v1")
MODEL = os.environ.get("CHAT_MODEL", "llama3")
SYSTEM_PROMPT = os.environ.get("CHAT_SYSTEM_PROMPT", "You are a helpful assistant.")

# MCP servers: JSON object mapping name -> SSE URL
# e.g. '{"mytools": "http://localhost:3000/sse"}'
MCP_SERVERS_JSON = os.environ.get("CHAT_MCP_SERVERS", "{}")

AI_PROMPT = "{._.} AI:\\> "

# Matches ANSI escape sequences: CSI (ESC[), OSC (ESC]), and other ESC-initiated codes
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;]*[A-Za-z]|\][^\x07]*(?:\x07|\x1b\\)|[()][A-B0-2]|[=>NOM78HD])")


def blinking_cursor(stop_event: threading.Event):
    """Show a blinking cursor until stop_event is set."""
    visible = True
    while not stop_event.is_set():
        if visible:
            sys.stdout.write("\u2588")  # solid block character
        else:
            sys.stdout.write(" ")
        sys.stdout.flush()
        sys.stdout.write("\b")  # move cursor back
        sys.stdout.flush()
        visible = not visible
        stop_event.wait(0.2)
    # Clear the cursor position
    sys.stdout.write(" \b")
    sys.stdout.flush()


class MCPManager:
    """Manages connections to MCP servers and exposes their tools."""

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._tool_to_server: dict[str, str] = {}  # tool_name -> server_name
        self._tools: list[dict] = []  # OpenAI-format tool definitions
        self._exit_stack = AsyncExitStack()

    async def connect(self, servers: dict[str, str]):
        """Connect to all configured MCP servers."""
        for name, url in servers.items():
            try:
                read_stream, write_stream = await self._exit_stack.enter_async_context(
                    sse_client(url)
                )
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                self._sessions[name] = session

                result = await session.list_tools()
                for tool in result.tools:
                    self._tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema,
                        },
                    })
                    self._tool_to_server[tool.name] = name
                print(f"  Connected to '{name}': {len(result.tools)} tool(s)")
            except Exception as e:
                print(f"  Failed to connect to '{name}' ({url}): {e}")

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call an MCP tool and return the result as a string."""
        server_name = self._tool_to_server.get(name)
        if not server_name or server_name not in self._sessions:
            return f"Error: unknown tool '{name}'"
        session = self._sessions[server_name]
        result = await session.call_tool(name, arguments)
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def close(self):
        await self._exit_stack.aclose()


async def stream_chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """Send messages to the API and stream the response.

    Returns a dict with 'content' (str) and 'tool_calls' (list) keys.
    """
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    full_content = ""
    tool_calls_by_index: dict[int, dict] = {}

    cursor_stop = threading.Event()
    cursor_thread = threading.Thread(target=blinking_cursor, args=(cursor_stop,), daemon=True)
    cursor_thread.start()

    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code != 200:
                cursor_stop.set()
                cursor_thread.join()
                body = (await response.aread()).decode()
                print(f"\n[Error {response.status_code}] {body}")
                return {"content": "", "tool_calls": []}

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})

                    # Handle text content
                    content = delta.get("content", "")
                    if content:
                        if not cursor_stop.is_set():
                            cursor_stop.set()
                            cursor_thread.join()
                        sanitized = _ANSI_RE.sub("", content)
                        print(sanitized, end="", flush=True)
                        full_content += sanitized

                    # Handle tool calls (streamed incrementally)
                    for tc in delta.get("tool_calls", []):
                        idx = tc["index"]
                        if idx not in tool_calls_by_index:
                            if not cursor_stop.is_set():
                                cursor_stop.set()
                                cursor_thread.join()
                            tool_calls_by_index[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        entry = tool_calls_by_index[idx]
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            entry["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            entry["function"]["arguments"] += fn["arguments"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    cursor_stop.set()
    cursor_thread.join()

    tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]

    if full_content:
        print()  # newline after streamed text

    return {"content": full_content, "tool_calls": tool_calls}


async def async_main():
    mcp = MCPManager()
    servers = json.loads(MCP_SERVERS_JSON)

    if servers:
        print("Connecting to MCP servers...")
        await mcp.connect(servers)

    print(f"Chat CLI  |  model: {MODEL}  |  endpoint: {BASE_URL}")
    if mcp.tools:
        print(f"Tools: {', '.join(t['function']['name'] for t in mcp.tools)}")
    print("Type /quit to exit, /clear to reset conversation.\n")

    messages: list[dict] = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})

    has_history = False
    try:
        while True:
            try:
                if has_history:
                    cols = shutil.get_terminal_size().columns
                    print(f"\n{'─' * cols}\n")
                user_input = (await asyncio.to_thread(input, "(o_o) YOU:\\> ")).strip()
                if has_history:
                    # Erase separator: up 3 lines (input, blank, separator),
                    # clear to end of screen, reprint input with spacing
                    sys.stdout.write("\033[3A\033[0J")
                    print(f"\nYOU:\\>{user_input}")
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            if not user_input:
                continue
            if user_input == "/quit":
                print("Bye!")
                break
            if user_input == "/clear":
                messages = []
                if SYSTEM_PROMPT:
                    messages.append({"role": "system", "content": SYSTEM_PROMPT})
                print("-- conversation cleared --\n")
                continue
            if user_input == "/tools":
                if mcp.tools:
                    for t in mcp.tools:
                        print(f"  {t['function']['name']}: {t['function']['description']}")
                else:
                    print("  No tools connected.")
                print()
                continue

            messages.append({"role": "user", "content": user_input})

            # Tool-call loop: keep going until the model produces a final text response
            first_turn = True
            while True:
                if first_turn:
                    print(AI_PROMPT, end="", flush=True)
                first_turn = False
                result = await stream_chat(messages, mcp.tools or None)

                if not result["tool_calls"]:
                    if result["content"]:
                        messages.append({"role": "assistant", "content": result["content"]})
                    has_history = True
                    break

                # Record the assistant message with tool calls
                assistant_msg: dict = {"role": "assistant", "tool_calls": result["tool_calls"]}
                if result["content"]:
                    assistant_msg["content"] = result["content"]
                else:
                    assistant_msg["content"] = None
                messages.append(assistant_msg)

                # Execute each tool call inside a visual frame
                print("\033[2m", end="")  # dim
                print("    ┌──────────────────────────────────────")
                for tc in result["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        fn_args = {}

                    args_preview = json.dumps(fn_args, ensure_ascii=False)
                    if len(args_preview) > 80:
                        args_preview = args_preview[:77] + "..."
                    print(f"    │ call  {fn_name}({args_preview})")

                    tool_result = _ANSI_RE.sub("", await mcp.call_tool(fn_name, fn_args))

                    result_preview = tool_result.replace("\n", " ")[:160]
                    if len(tool_result) > 160:
                        result_preview += "..."
                    print(f"    │ result {result_preview}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result,
                    })
                print("    └──────────────────────────────────────")
                print("\033[0m", end="")  # reset
                print()
                print()
                print(AI_PROMPT, end="", flush=True)
    finally:
        await mcp.close()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
