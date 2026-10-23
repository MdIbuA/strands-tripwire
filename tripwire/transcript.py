"""Build decider states from a Strands message list without mixing up who said what.

Strands stores tool results inside `user`-role messages, so a naive flatten makes an
injected email look like the user talking. `split_provenance` keeps the three channels
apart; `render_intent_state` then shows the decider only the trusted one.
"""

from __future__ import annotations

import json
from typing import Any

# Free-text arguments are left out of the action: they carry the agent's own prose, which
# says nothing about *who* asked for the call and only dilutes the question.
FREE_TEXT_ARGS = {"body", "content", "text", "message"}


def _tool_result_text(block: dict[str, Any]) -> str:
    parts = []
    for item in block.get("content", []):
        if "text" in item:
            parts.append(item["text"])
        elif "json" in item:
            parts.append(json.dumps(item["json"]))
    return "\n".join(parts)


def split_provenance(messages: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """Return (user_typed, assistant_said, content_read) as lists of plain text."""
    user_typed: list[str] = []
    assistant_said: list[str] = []
    content_read: list[str] = []
    for message in messages:
        role = message.get("role")
        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if "toolResult" in block:
                content_read.append(_tool_result_text(block["toolResult"]))
            elif "text" in block:
                (user_typed if role == "user" else assistant_said).append(block["text"])
    return user_typed, assistant_said, content_read


def describe_action(tool_name: str, args: dict[str, Any]) -> str:
    shown = {k: v for k, v in args.items() if k not in FREE_TEXT_ARGS}
    return f"{tool_name}({json.dumps(shown, ensure_ascii=False)})"


def render_intent_state(messages: list[dict[str, Any]], tool_name: str, args: dict[str, Any]) -> str:
    """What the user typed + the proposed action. Deliberately nothing the agent read."""
    user_typed, _, _ = split_provenance(messages)
    return (
        "A user typed a request to their AI assistant. The assistant then proposed an action.\n\n"
        f"User typed: {chr(10).join(user_typed) or '(nothing)'}\n\n"
        f"Assistant proposes: {describe_action(tool_name, args)}"
    )
