def initial_chat_title(text: str) -> str:
    """Use the first six words, bounded for sidebar display."""
    words = text.split()
    title = " ".join(words[:6])
    if not title:
        return "Yeni sohbet"
    shortened = len(words) > 6 or len(title) > 60
    return title[:60].rstrip() + ("…" if shortened else "")
