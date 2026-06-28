import asyncio
import time
from typing import Dict
from google import genai

# 1. Client Tek Sefer Oluşturuluyor
client = genai.Client(
    vertexai=True,
    project="gen-lang-client-0430976171",
    location="europe-west1"
)

ACTIVE_SESSIONS: Dict[str, any] = {}

async def get_or_create_chat(user_id: str):
    """Kullanıcıya özel asenkron chat objesini getirir veya oluşturur."""
    if user_id not in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[user_id] = client.aio.chats.create(
            model="gemini-2.5-flash",
            config={
                "temperature": 0.7,
                "thinking_config": {"thinking_budget": 0},
                "max_output_tokens": 4512
            }
        )
    return ACTIVE_SESSIONS[user_id]

async def warmup_connection(user_id: str):
    """İlk mesaj yavaşlığını (Cold Start) önlemek için gizli bir ısınma turu atar."""
    print(f"[SYSTEM] {user_id} için bağlantı ısıtılıyor (Lütfen bekleyin)...")
    chat = await get_or_create_chat(user_id)
    try:
        # Sadece bir nokta gönderip bağlantıyı açtırıyoruz, cevabı çöpe atıyoruz
        response = await chat.send_message_stream(".")
        async for _ in response:
            pass 
        print(f"[SYSTEM] BAĞLANTI ISINDI! Artık ilk mesajın da uçak gibi olacak.")
    except Exception as e:
        print(f"[SYSTEM] Isınma hatası: {e}")

async def process_interactive_request(user_id: str, message: str):
    """Senin terminalde tek başına yazışman için (anında ekrana basar)."""
    start_time = time.monotonic()
    chat = await get_or_create_chat(user_id)
    
    response = await chat.send_message_stream(message)
    
    print("AI: ", end="", flush=True)
    async for chunk in response:
        print(chunk.text, end="", flush=True)
        
    elapsed = time.monotonic() - start_time
    print(f"\n  ⏱ Süre: {elapsed:.2f}s")

async def process_test_request(user_id: str, message: str):
    """3 kişilik toplu test için (yazıları birbirine karıştırmadan topluca basar)."""
    start_time = time.monotonic()
    chat = await get_or_create_chat(user_id)
    print(f"[{user_id}] İsteği Google'a gönderildi...")
    
    response = await chat.send_message_stream(message)
    
    tam_cevap = ""
    async for chunk in response:
        tam_cevap += chunk.text
        
    elapsed = time.monotonic() - start_time
    print(f"\n[{user_id}] AI (Süre: {elapsed:.2f}s):\n{tam_cevap}\n{'-'*40}")

async def main():
    my_user_id = "Kursat_01"
    
    # KOD BAŞLAR BAŞLAMAZ BAĞLANTIYI ISITIYORUZ! (Cold Start'ı Yok Ediyoruz)
    await warmup_connection(my_user_id)
    
    print("\n" + "="*50)
    print(" ORION ASENKRON TERMİNAL HAZIR")
    print(" 1. Normal sohbet edebilirsin.")
    print(" 2. Çoklu paralel testi çalıştırmak için '!test' yaz.")
    print(" 3. Çıkmak için 'q' yaz.")
    print("="*50)
    
    while True:
        # Asenkron yapıyı dondurmamak için to_thread kullanıyoruz
        user_input = await asyncio.to_thread(input, "\nSen: ")
        
        if not user_input.strip():
            continue
            
        if user_input.lower() == 'q':
            print("Sistem kapatılıyor...")
            break
            
        if user_input.lower() == '!test':
            print("\n--- 3 KİŞİLİK PARALEL TEST BAŞLIYOR ---")
            await asyncio.gather(
                process_test_request("Ali_01", "Bana uzun bir hikaye anlatır mısın?"),
                process_test_request("Ayse_02", "Python nedir?"),
                process_test_request("Mehmet_03", "Nasılsın Orion?")
            )
            continue
        
        # Kullanıcının girdiği normal metni işliyoruz
        await process_interactive_request(my_user_id, user_input)

if __name__ == "__main__":
    asyncio.run(main())