"""Failed turns belong to the live view, never to conversation history."""


def successful_history(history: list[dict]) -> list[dict]:
    groups = []
    for message in history:
        if message.get('role') == 'user' or not groups:
            groups.append([])
        groups[-1].append(message)
    prefixes = (
        '⚠️ **Yönlendirici (Router) veya AI Modeline Bağlanılamadı',
        '⚠️ **Model Yanıt Zaman Aşımı**',
        '⚠️ **Yönlendirici (Router) Hatası:**',
    )
    return [message for group in groups
            if not any(item.get('failed_turn') or item.get('generation_error') or
                       (item.get('role') == 'assistant' and
                        isinstance(item.get('content'), str) and
                        item['content'].startswith(prefixes)) for item in group)
            for message in group]
