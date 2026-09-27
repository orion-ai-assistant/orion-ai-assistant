from orion.worker.services.attachments import history_content
import json


def _without_direct_copy(group):
    """Remove only display copies backed by a successful paired Google result."""
    from orion.worker.services.tool_runner import google_direct_text

    google_ids = {call['id'] for msg in group for call in msg.get('tool_calls', [])
                  if call.get('function', {}).get('name') == 'google_ai_mode'}
    results = {}
    projected = []
    for msg in group:
        if msg.get('role') == 'tool' and msg.get('tool_call_id') in google_ids:
            try:
                result = json.loads(msg.get('content', ''))
            except (ValueError, TypeError):
                result = None
            if (isinstance(result, dict) and result.get('status') in ('ok', 'partial')
                    and isinstance(result.get('answer'), str) and result['answer']):
                results[msg['tool_call_id']] = result
        if msg.get('role') == 'assistant' and not msg.get('tool_calls'):
            if msg.get('direct_tool_call_id') in results:
                continue
            # Existing saved turns predate the marker. Require an exact display match,
            # not a heading/substring heuristic that could delete a normal summary.
            if any(history_content(msg) == google_direct_text(result['answer'], result)
                   for result in results.values()):
                continue
        projected.append(msg)
    return projected


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
        group = _without_direct_copy(group)
        if kept and count + len(group) > max_messages:
            break
        kept.insert(0, group)
        count += len(group)
    return [{key: (history_content(msg) if key == "content" else msg[key])
             for key in ("role", "content", "tool_calls", "tool_call_id") if key in msg}
            for group in kept for msg in group]
