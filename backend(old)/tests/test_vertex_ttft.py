import time
from google import genai

# Mevcut test dosyalarından alınan proje ID'si
PROJECT_ID = "orionai-assistant-2604"
LOCATION = "europe-west3"
MODEL = "gemini-2.5-flash"

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

chat = client.chats.create(
    model=MODEL,
    config={
        "thinking_config": {"thinking_budget": 0},
        "max_output_tokens": 512,
    },
)


def main():
    print("[Bilgi] Vertex AI chat bağlantısı başlatıldı.")
    print("[Bilgi] Aynı sohbet oturumu üzerinden istediğin kadar soru sorabilirsin.")
    print("Çıkmak için 'exit' ya da 'quit' yaz.")

    while True:
        prompt = input("Sen: ")
        if not prompt:
            continue
        if prompt.lower().strip() in ["exit", "quit"]:
            break

        start_time = time.time()
        first_token_received = False

        print("AI: ", end="", flush=True)

        response = chat.send_message_stream(
            prompt,
            config={
                "thinking_config": {"thinking_budget": 0},
            },
        )

        for chunk in response:
            if not first_token_received:
                first_token_time = time.time()
                ttft = first_token_time - start_time
                print(f"\n[BAŞARILI] İlk token geldi! TTFT: {ttft * 1000:.0f} ms")
                first_token_received = True

            if chunk.text:
                print(chunk.text, end="", flush=True)

        end_time = time.time()
        total_time = end_time - start_time
        print(f"\n--- Özet ---")
        print(f"İlk Token Süresi (TTFT): {ttft * 1000:.0f} ms")
        print(f"Toplam Süre:             {total_time:.0f} ms")


if __name__ == "__main__":
    main()
