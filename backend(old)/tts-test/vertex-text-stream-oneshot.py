from google import genai
from google.genai import types # SDK'nın kendi veri tiplerini dahil ettik
import time

client = genai.Client(
    vertexai=True,
    project="orion-test-free",
    location="europe-west3"
)

gecmis = []

print("🤖 Orion Terminal Testi (Stateless - Optimize Edilmiş Hız)")

# Senin orijinal ayarlarını birebir buraya taşıdık
opt_config = {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "max_output_tokens": 5512,
    "thinking_config": {"thinking_budget": 0}
}

while True:
    user_input = input("\nSen: ")
    if not user_input or user_input.lower() == 'q':
        break

    # DİKKAT: Artık dict {} yerine doğrudan Content objesi oluşturuyoruz.
    # Redis'ten çekerken de veriyi bu formata mapleyebilirsin.
    gecmis.append(
        types.Content(role="user", parts=[types.Part.from_text(text=user_input)])
    )

    start_time = time.monotonic()

    response_stream = client.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=gecmis,
        config=opt_config
    )

    print("AI: ", end="", flush=True)

    tam_cevap = ""
    for chunk in response_stream:
        print(chunk.text, end="", flush=True)
        tam_cevap += chunk.text

    print()

    elapsed = time.monotonic() - start_time
    print(f"  ⏱ Toplam süre: {elapsed:.2f}s")

    # Çıktıyı da yine orijinal veri tipiyle geçmişe ekliyoruz
    gecmis.append(
        types.Content(role="model", parts=[types.Part.from_text(text=tam_cevap)])
    )