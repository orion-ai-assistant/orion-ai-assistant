class DisplayTimeline:
    """Keep thinking, visible text and tools in the order they appeared."""
    def __init__(self):
        self.parts = []

    def add(self, kind: str, value: str = "") -> None:
        if kind == "snapshot":
            previous = sum(len(part["content"]) for part in self.parts
                           if part["type"] == "content" and part is not self.parts[-1]) if self.parts else 0
            if self.parts and self.parts[-1]["type"] == "content":
                self.parts[-1]["content"] = value[previous:]
            elif value:
                self.parts.append({"type": "content", "content": value[previous:]})
        elif kind in ("thinking", "content"):
            if self.parts and self.parts[-1]["type"] == kind:
                self.parts[-1]["content"] += value
            else:
                self.parts.append({"type": kind, "content": value})
        elif kind == "tool_call":
            self.parts.append({"type": "tool", "call_id": value})
