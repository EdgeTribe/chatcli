#!/usr/bin/env python3
"""Simple CLI chat client for OpenAI-compatible servers."""

import json
import os
import sys
import threading
import time

import httpx

# Configuration via environment variables
API_KEY = os.environ.get("CHAT_API_KEY", "")
BASE_URL = os.environ.get("CHAT_BASE_URL", "http://localhost:11434/v1")
MODEL = os.environ.get("CHAT_MODEL", "llama3")
SYSTEM_PROMPT = os.environ.get("CHAT_SYSTEM_PROMPT", "You are a helpful assistant.")


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


def stream_chat(messages: list[dict]) -> str:
    """Send messages to the API and stream the response."""
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
    }

    full_response = ""
    cursor_stop = threading.Event()
    cursor_thread = threading.Thread(target=blinking_cursor, args=(cursor_stop,), daemon=True)
    cursor_thread.start()

    with httpx.Client(timeout=120) as client:
        with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code != 200:
                cursor_stop.set()
                cursor_thread.join()
                body = response.read().decode()
                print(f"\n[Error {response.status_code}] {body}")
                return ""

            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        if not cursor_stop.is_set():
                            cursor_stop.set()
                            cursor_thread.join()
                        print(content, end="", flush=True)
                        full_response += content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    cursor_stop.set()
    cursor_thread.join()
    print()  # newline after streamed response
    return full_response


def main():
    print(f"Chat CLI  |  model: {MODEL}  |  endpoint: {BASE_URL}")
    print("Type /quit to exit, /clear to reset conversation.\n")

    messages = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})

    while True:
        try:
            user_input = input("You> ").strip()
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

        messages.append({"role": "user", "content": user_input})

        print("AI> ", end="", flush=True)
        reply = stream_chat(messages)

        if reply:
            messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
