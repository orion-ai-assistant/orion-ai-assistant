from google import genai
import time

# Client sadece 1 kere oluşturulur (çok önemli)
client = genai.Client(
    vertexai=True,
    project="orion-test-free",
    location="europe-west3"
)
# europe-west3 en hızlısı bu olabilir
# Chat session (stateful konuşma için)
chat = client.chats.create(
    model="gemini-2.5-flash",
    config={
        # 🔧 model davranış ayarları
        "temperature": 0.7,        # yaratıcılık
        "top_p": 0.9,
        "top_k": 40,

        # 🧠 thinking / reasoning kontrolü (varsa)
        # bazı modellerde bu parametre desteklenir
        "thinking_config": {
            "thinking_budget": 0    # düşük → daha hızlı cevap
        },

        # ⚡ hız odaklı ayarlar
        "max_output_tokens": 5512
    }
)

# sürekli sohbet
while True:
    user_input = input("\nSen: ")
    if not user_input:
        continue

    start_time = time.monotonic()

    response = chat.send_message_stream(user_input)

    print("AI: ", end="", flush=True)

    for chunk in response:
        # chunk.text = stream edilen parça
        print(chunk.text, end="", flush=True)

    print()  # satır sonu

    elapsed = time.monotonic() - start_time
    print(f"  ⏱ Toplam süre: {elapsed:.2f}s")