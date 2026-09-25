import json


class ToolCallAccumulator:
    """Accumulate a single choice; execute only after an explicit tool finish."""
    def __init__(self):
        self.calls = {}
        self.finish_reason = None
        self.done = False

    def feed(self, choice):
        fragments = (choice.get("delta") or {}).get("tool_calls") or []
        for fragment in fragments:
            index = fragment.get("index")
            if not isinstance(index, int) or not 0 <= index < 16:
                raise RuntimeError("Invalid tool call index or call limit exceeded")
            call = self.calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            call["id"] += fragment.get("id") or ""
            function = fragment.get("function") or {}
            for key in ("name", "arguments"):
                call["function"][key] += function.get(key) or ""
        aggregate = (choice.get("message") or {}).get("tool_calls")
        if aggregate is not None:
            self.calls = dict(enumerate(aggregate))
        if len(self.calls) > 16 or len(json.dumps(self.calls).encode()) > 128 * 1024:
            raise RuntimeError("Tool call payload limit exceeded")
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        return bool(fragments or aggregate)

    def complete(self):
        if not self.calls:
            return []
        if not self.done or self.finish_reason not in ("tool_calls", "stop"):
            raise RuntimeError("Incomplete tool stream: missing terminal marker or finish reason")
        calls = [self.calls[key] for key in sorted(self.calls)]
        ids = set()
        for call in calls:
            if (not call.get("id") or call["id"] in ids or call.get("type", "function") != "function"
                or not call.get("function", {}).get("name")
                or not isinstance(call["function"].get("arguments"), str)):
                raise RuntimeError("Malformed tool call")
            ids.add(call["id"])
        return calls
