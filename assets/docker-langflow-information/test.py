import requests
import uuid
import json

# Flow API endpoint (stream=true parametresi ekliyoruz)
url = "http://localhost:7860/api/v1/run/fc65ba5f-9da7-450a-852c-315b7fe4fc2a?stream=true"

# WebUI token / API key
token = "sk-J3XzvNjhCkvbMFVnstxGTcPjpOHeJJgcOxUBu-Iyzf4"
# Headers
headers = {
    "Content-Type": "application/json",
    "x-api-key": token
}

# Payload
payload = {
    "output_type": "chat",
    "input_type": "chat",
    "input_value": "2",
    "session_id": str(uuid.uuid4())
}

try:
    # Stream destekli POST isteği
    with requests.post(url, json=payload, headers=headers, stream=True) as response:
        response.raise_for_status()  # Hata varsa exception fırlat

        llm_text = ""
        print("Cevap akıyor...")

        # Stream'i satır satır oku
        for line in response.iter_lines():
            if line:
                try:
                    data = json.loads(line.decode('utf-8'))
                    # Eğer event token ise, chunk'ı ekle
                    if data.get("event") == "token":
                        chunk = data.get("data", {}).get("chunk", "")
                        llm_text += chunk
                        print(chunk, end="", flush=True)
                    # Eğer event add_message ve sender Machine ise, tam mesaj
                    if data.get("event") == "add_message" and data.get("data", {}).get("sender") == "Machine":
                        llm_text = data["data"]["text"]
                except json.JSONDecodeError:
                    continue

        print("\n\nTam cevap:\n", llm_text)

except requests.exceptions.RequestException as e:
    print(f"API isteği hatası: {e}")
