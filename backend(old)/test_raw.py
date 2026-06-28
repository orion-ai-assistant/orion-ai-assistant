# test_raw.py
from langgraph_sdk import get_client
import asyncio
import uuid

async def test():
    # Docker dışından (Windows'tan) bağlandığın için localhost:8123 doğrudur
    client = get_client(url="http://localhost:8123")
    
    # Loglarından aldığım gerçek asistan ID'si
    REAL_ASSISTANT_ID = "fe096781-5601-53d2-b2f6-0d3403f7e9ca" 
    
    new_thread_id = str(uuid.uuid4())
    print(f"--- Test Başlıyor (Thread: {new_thread_id}) ---")

    try:
        async for chunk in client.runs.stream(
            thread_id=new_thread_id,
            assistant_id=REAL_ASSISTANT_ID, 
            input={"messages": [{"role": "user", "content": "Selam, 3 kelimelik cevap ver."}]},
            stream_mode=["messages", "updates"]
        ):
            # Sadece RAW veriyi basıyoruz
            event = chunk.get('event')
            data = chunk.get('data')
            
            if event == "messages/partial":
                # Buradaki 'content' büyüyecek mi yoksa tekil mi gelecek?
                print(f"\n[MSG RAW]: {data}")
            elif event == "updates":
                print(f"\n[UPD RAW]: {data}")
            else:
                print(f".", end="", flush=True)
                
    except Exception as e:
        print(f"\nHata Detayı: {e}")

if __name__ == "__main__":
    asyncio.run(test())