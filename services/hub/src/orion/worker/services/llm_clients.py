"""
Backward compatibility shim — tüm import'lar artık router.py'den gelir.
Eski kodlarda `from orion.worker.services.llm_clients import ...` şeklinde
yapılmış import'ların çalışmaya devam etmesini sağlar.
"""
from orion.worker.services.router import (  # noqa: F401
    llama_chat,
    llama_stream_chat,
    tei_embed,
    generate_embeddings,
    generate_tts,
)
