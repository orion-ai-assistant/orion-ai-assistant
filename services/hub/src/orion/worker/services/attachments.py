"""Ordered Hub content contract; provider-specific translation belongs to Router."""
from typing import Any

from orion.contracts.http import JobInput


def media_part(data: str) -> dict[str, Any]:
    if data.startswith("data:"):
        header, separator, encoded = data.partition(",")
        mime = header[5:].split(";", 1)[0].lower()
        if not separator or not header.endswith(";base64"):
            raise ValueError("Media must use a base64 data URL")
        if mime.startswith(("audio/", "video/")):
            kind = "input_audio" if mime.startswith("audio/") else "input_video"
            subtype = mime.split("/", 1)[1]
            audio_formats = {"mpeg": "mp3", "x-wav": "wav", "wave": "wav", "vnd.wave": "wav", "x-flac": "flac"}
            fmt = audio_formats.get(subtype, subtype) if kind == "input_audio" else subtype
            return {"type": kind, kind: {"data": encoded, "format": fmt}}
        if not mime.startswith("image/"):
            raise ValueError(f"Unsupported media MIME type: {mime}")
    elif not data.startswith(("http://", "https://")):
        # Preserve the original raw-JPEG input contract for older clients.
        data = f"data:image/jpeg;base64,{data}"
    return {"type": "image_url", "image_url": {"url": data}}


def user_content(value: JobInput) -> str | list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if value.attachments is not None:
        for item in value.attachments:
            if item.isText:
                parts.append({"type": "text", "text": f"[Ek Dosya: {item.name}]\n{item.data}"})
            else:
                parts.append(media_part(item.data))
    else:
        parts.extend(media_part(data) for data in value.images or [])
    if not parts:
        return value.text
    parts.append({"type": "text", "text": value.text})
    return parts


def history_content(message: dict[str, Any]) -> Any:
    """Upgrade old UI turns in memory without rewriting persisted history."""
    content = message.get("model_content", message.get("content"))
    if message.get("role") != "user" or "model_content" in message:
        return content
    attachments = message.get("attachments")
    display_text = message.get("display_text")
    if attachments and isinstance(display_text, str) and all(
        isinstance(item, dict) and isinstance(item.get("data"), str)
        and (item.get("isText") or item["data"].startswith("data:"))
        for item in attachments
    ):
        # Old UI metadata still has the cross-media order even though its content
        # array grouped media first and appended every text file to the prompt.
        parts = [
            {"type": "text", "text": f"[Ek Dosya: {item.get('name', 'Dosya')}]\n{item['data']}"}
            if item.get("isText") else media_part(item["data"])
            for item in attachments
        ]
        return parts + [{"type": "text", "text": display_text or "Ekleri incele."}]
    if isinstance(content, list):
        return [
            media_part(part["image_url"]["url"])
            if part.get("type") == "image_url"
            and part.get("image_url", {}).get("url", "").startswith(("data:audio/", "data:video/"))
            else part for part in content
        ]
    return content
