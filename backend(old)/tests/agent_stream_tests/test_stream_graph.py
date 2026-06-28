"""Manual graph stream test helper.

Bu dosya şu amacı taşır:
- On top of `agent.graph` / `stream_request` çalıştırır ve eventları toplar.
- `think`, `text`, `final` eventlarını yazdırır.
- Stream veri kesme/senkronizasyon hatalarını analiz etmede yardımcı.

Çalıştırmak için:
    python tests/manual/test_stream_graph.py
"""

from agent.facade import stream_request
from langchain_core.messages import HumanMessage
import asyncio

async def main():
    messages = [HumanMessage(content="Say the word 'echo' exactly 5 times over multiple chunks.")]
    
    print("Streaming started.")
    async for event in stream_request(messages=messages):
        print(event)
    print("DONE")

if __name__ == "__main__":
    asyncio.run(main())
