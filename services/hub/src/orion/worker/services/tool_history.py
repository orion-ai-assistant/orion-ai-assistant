def model_history(history, max_messages):
    """Keep whole user turns, including paired calls/results, at the boundary."""
    groups = []
    for message in history:
        if message.get("role") == "user" or not groups:
            groups.append([])
        if groups:
            groups[-1].append(message)
    kept = []
    count = 0
    for group in reversed(groups):
        if group[0].get("failed_turn"):
            continue
        ids = [call["id"] for msg in group for call in msg.get("tool_calls", [])]
        results = [msg.get("tool_call_id") for msg in group if msg.get("role") == "tool"]
        if ids != results:
            continue
        if kept and count + len(group) > max_messages:
            break
        kept.insert(0, group)
        count += len(group)
    return [{key: (msg.get("model_content", msg.get("content")) if key == "content" else msg[key])
             for key in ("role", "content", "tool_calls", "tool_call_id") if key in msg}
            for group in kept for msg in group]
