from __future__ import annotations

import re


def normalize_chunk(text: str) -> str:
    """Normalize extra newlines to single newlines for chunk text."""
    if not text:
        return ""
    return re.sub(r"\n[ \t\r]*\n+", "\n", text)


def strip_extras_from_content_block(content):
    """Remove provider extras and index meta from content blocks."""
    if isinstance(content, list):
        cleaned = []
        for block in content:
            if isinstance(block, dict):
                cleaned.append({k: v for k, v in block.items() if k not in {"extras", "index"}})
            else:
                cleaned.append(block)
        return cleaned
    return content


def join_thoughts_for_log(parts: list[str]) -> str:
    """Join thought blocks with normalization."""
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    return normalize_chunk("\n\n".join(cleaned)).strip()


def content_to_text_and_thoughts(content) -> tuple[str, str]:
    """Convert AI message content to plain text and thought text."""
    if isinstance(content, str):
        return content, ""

    if isinstance(content, list):
        text_parts: list[str] = []
        thought_parts: list[str] = []

        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, dict):
                continue

            block_type = str(block.get("type", "")).lower()
            if block_type in {"thinking", "thought"} or bool(block.get("thought")):
                thought = block.get("thinking") or block.get("thought")
                if isinstance(thought, str) and thought:
                    thought_parts.append(thought)
                continue

            text = block.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)

        text = "".join(text_parts)
        thoughts = "\n".join(thought_parts)

        if not text and thoughts:
            text = thoughts

        return text, thoughts

    return "", ""
