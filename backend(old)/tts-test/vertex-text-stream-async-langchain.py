import time
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# 1. LLM İstemcisini (Client) Oluşturma
# vertexai=True diyerek doğrudan Google Cloud (Vertex AI) altyapısına bağlanıyoruz
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    vertexai=True,
    project="gen-lang-client-0430976171",
    location="europe-west1",
    temperature=0.7,
    max_output_tokens=5512,
    streaming=True  # Akış özelliğini LLM seviyesinde açıyoruz
)

# 2. LangChain için Manuel Sohbet Geçmişi (Hafıza)
# Google native SDK bunu kendi içinde saklıyordu, LangChain'de biz yönetiyoruz.
chat_history = [
    SystemMessage(content="Sen yardımsever ve zeki bir asistansın. Kısa ve öz cevaplar ver.")
]

print("="*50)
print(" LANGCHAIN + VERTEX AI SOHBET BAŞLADI")
print(" Çıkmak için 'q' yazın.")
print("="*50)

# 3. Sürekli Sohbet Döngüsü
while True:
    user_input = input("\nSen: ")
    if not user_input.strip():
        continue
    if user_input.lower() == 'q':
        print("Sistem kapatılıyor...")
        break

    start_time = time.monotonic()

    # Kullanıcının mesajını geçmişe ekliyoruz
    chat_history.append(HumanMessage(content=user_input))

    print("AI: ", end="", flush=True)
    
    full_response = ""
    
    try:
        # LLM'e tüm mesaj geçmişini gönderip akışı (stream) dinliyoruz
        for chunk in llm.stream(chat_history):
            if chunk.content:
                print(chunk.content, end="", flush=True)
                full_response += chunk.content
    except Exception as e:
        print(f"\n[HATA] Bir sorun oluştu: {e}")
        # Hata olursa eklediğimiz son mesajı geri alalım ki geçmiş bozulmasın
        chat_history.pop()
        continue

    print()  # Satır sonu

    # Asistanın verdiği cevabı da geçmişe (hafızaya) ekliyoruz
    chat_history.append(AIMessage(content=full_response))

    elapsed = time.monotonic() - start_time
    print(f"  ⏱ Toplam süre (LangChain): {elapsed:.2f}s")