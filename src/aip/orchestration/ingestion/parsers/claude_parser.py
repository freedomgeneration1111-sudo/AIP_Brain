"""Parser for Claude.ai conversation export format (conversations.json).
Converts Claude export turns into CorpusTurn objects for corpus storage.
Pure function — does not write to any store.

Claude export structure:
  conversations.json = list of conversation objects
  conversation keys: uuid, name, summary, created_at, updated_at,
                     account, chat_messages
  chat_messages keys: uuid, text, content, sender, created_at,
                      updated_at, attachments, files, parent_message_uuid
  sender values: 'human' | 'assistant'
  content: str OR list of typed blocks
  content block types: text, thinking, tool_use, tool_result, token_budget
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from aip.foundation.schemas.corpus_turn import CorpusTurn, make_turn_id


def _extract_text_and_thinking(message: dict) -> tuple[str, str]:
    """
    Extract (assistant_text_or_user_text, thinking_text) from a message.

    Returns (content_text, thinking_text).
    thinking_text is only populated for assistant messages that contain
    thinking blocks. For human messages thinking_text is always "".

    Priority:
    1. If message["content"] is a non-empty list: process as blocks
    2. Elif message["content"] is a non-empty string: use directly
    3. Elif message["text"] is a non-empty string: use as fallback
    4. Else: return ("", "")
    """
    content = message.get("content", "")
    text_fallback = message.get("text", "")

    if isinstance(content, list) and content:
        text_parts: list[str] = []
        thinking_parts: list[str] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")

            if block_type == "text":
                t = block.get("text", "").strip()
                if t:
                    text_parts.append(t)

            elif block_type == "thinking":
                # Preserve extended thinking — often more valuable than answer
                t = block.get("thinking", "").strip()
                if not t:
                    t = block.get("text", "").strip()
                if t:
                    thinking_parts.append(t)

            elif block_type in ("tool_use", "tool_result", "token_budget"):
                # Not preserved in corpus — skip silently
                pass

            else:
                # Unknown future block type — skip silently
                pass

        return (
            " ".join(text_parts).strip(),
            " ".join(thinking_parts).strip(),
        )

    elif isinstance(content, str) and content.strip():
        return (content.strip(), "")

    elif isinstance(text_fallback, str) and text_fallback.strip():
        return (text_fallback.strip(), "")

    return ("", "")


def parse_claude_export(
    file_path: str,
    source_account: str,
    export_date: str,
) -> tuple[list[CorpusTurn], list[str]]:
    """
    Parse a Claude conversations.json export file.

    Args:
        file_path: Path to conversations.json
        source_account: Identifier for this export batch
        export_date: ISO date string of when export was made

    Returns:
        tuple of:
          - list[CorpusTurn]: all parsed turns, ready to write
          - list[str]: warnings for non-fatal issues

    Raises:
        FileNotFoundError: if file_path does not exist
        ValueError: if file is not valid JSON, not a list,
                    or contains 0 conversations
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            data: Any = json.load(f)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {file_path}: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Expected list of conversations in {file_path}, got {type(data)}")

    if len(data) == 0:
        raise ValueError(f"No conversations found in {file_path}")

    turns: list[CorpusTurn] = []
    warnings: list[str] = []

    for conv in data:
        if not isinstance(conv, dict):
            warnings.append("non-dict conversation entry skipped")
            continue

        conv_id = conv.get("uuid") or conv.get("id")
        if not conv_id:
            conv_id = str(uuid.uuid4())
            warnings.append(f"conversation missing uuid, generated: {conv_id[:8]}")

        conv_name = (conv.get("name") or "").strip()
        if not conv_name:
            conv_name = f"Untitled conversation {conv_id[:8]}"

        conv_timestamp = conv.get("created_at") or conv.get("updated_at") or ""

        messages = conv.get("chat_messages") or []
        if not isinstance(messages, list):
            messages = []

        if not messages:
            warnings.append(f"conv {conv_id[:8]} '{conv_name[:30]}': no messages, skipped")
            continue

        # Turn pairing logic - state machine for non-alternating senders
        current_user_text = ""
        turn_index = 0

        for message in messages:
            if not isinstance(message, dict):
                warnings.append(f"conv {conv_id[:8]}: non-dict message skipped")
                continue

            sender = message.get("sender", "")
            content_text, thinking_text = _extract_text_and_thinking(message)

            if sender == "human":
                if current_user_text:
                    # Previous human turn had no assistant response
                    turns.append(
                        CorpusTurn(
                            turn_id=make_turn_id(conv_id, turn_index),
                            conversation_id=conv_id,
                            conversation_name=conv_name,
                            turn_index=turn_index,
                            source_model="claude",
                            source_account=source_account,
                            export_date=export_date,
                            user_text=current_user_text,
                            assistant_text="",
                            thinking_text="",
                            turn_timestamp=conv_timestamp,
                        )
                    )
                    warnings.append(f"conv {conv_id[:8]} turn {turn_index}: human message with no assistant response")
                    turn_index += 1

                current_user_text = content_text

                if not content_text:
                    warnings.append(f"conv {conv_id[:8]}: empty human message skipped")
                    current_user_text = ""

            elif sender == "assistant":
                if not current_user_text:
                    # Assistant message with no preceding human message
                    warnings.append(
                        f"conv {conv_id[:8]} turn {turn_index}: "
                        f"assistant message with no preceding human message, skipped"
                    )
                    continue

                # Valid turn
                turns.append(
                    CorpusTurn(
                        turn_id=make_turn_id(conv_id, turn_index),
                        conversation_id=conv_id,
                        conversation_name=conv_name,
                        turn_index=turn_index,
                        source_model="claude",
                        source_account=source_account,
                        export_date=export_date,
                        user_text=current_user_text,
                        assistant_text=content_text,
                        thinking_text=thinking_text,
                        turn_timestamp=message.get("created_at", conv_timestamp),
                    )
                )
                turn_index += 1
                current_user_text = ""

            else:
                warnings.append(f"conv {conv_id[:8]}: unknown sender '{sender}', message skipped")

        if current_user_text:
            # Conversation ended on human message
            turns.append(
                CorpusTurn(
                    turn_id=make_turn_id(conv_id, turn_index),
                    conversation_id=conv_id,
                    conversation_name=conv_name,
                    turn_index=turn_index,
                    source_model="claude",
                    source_account=source_account,
                    export_date=export_date,
                    user_text=current_user_text,
                    assistant_text="",
                    thinking_text="",
                    turn_timestamp=conv_timestamp,
                )
            )
            warnings.append(f"conv {conv_id[:8]}: conversation ends on human message")

    return turns, warnings
