"""Manual stream test helper.

Bu dosya şu amacı taşır:
- Model katmanındaki `llm.astream(...)` akışını doğrudan test eder.
- Her gelen chunk'ın `content` değerini yazdırır.
- Üretim yerine manuel doğrulama için hızlı debug scriptidir.

Çalıştırmak için:
    python tests/manual/test_stream.py
"""

from agent.model import get_model
from langchain_core.messages import HumanMessage
import asyncio

async def main():
    llm = get_model()
    messages = [HumanMessage(content="Say the word 'echo' 5 times.")]
    
    print("Streaming started.")
    chunks = []
    async for chunk in llm.astream(messages):
        chunks.append(chunk.content)
        print(f"CHUNK: {repr(chunk.content)}")
    print("DONE")

if __name__ == "__main__":
    asyncio.run(main())
