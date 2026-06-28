import base64
import json
import urllib.request
import os
import sys

# Ayarlar
URL_EMBEDDINGS = "http://localhost:8081/embeddings"
URL_CHAT = "http://localhost:8081/v1/chat/completions"
DEFAULT_IMAGE = "img.jpg"

def get_base64_image(image_path):
    if not os.path.exists(image_path):
        print(f"❌ Hata: {image_path} bulunamadı!")
        return None
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def test_embedding_v2(image_path):
    """
    Standart multimodal embedding formatı.
    'content' string olmalı, görsel 'image_data' içinde gitmeli.
    """
    print(f"\n--- Multimodal Embedding Testi (Format V2) ---")
    img_b64 = get_base64_image(image_path)
    if not img_b64: return

    # Sunucu loglarındaki 'type must be string' hatasını çözmek için 'content' string yapıldı.
    payload = {
        "content": "", 
        "image_data": [
            {
                "data": img_b64,
                "id": 0
            }
        ]
    }

    req = urllib.request.Request(
        URL_EMBEDDINGS,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print("✅ Sunucu yanıt verdi!")
            
            # Liste gelirse ilk elemanı al, sözlük gelirse direkt kullan
            res_obj = result[0] if isinstance(result, list) else result
            
            # Yanıt yapısını kontrol et (data: [...] veya direkt embedding: [...])
            data = res_obj.get("data", [])
            if data and len(data) > 0:
                embedding = data[0].get("embedding", [])
                print(f"✅ Başarılı! Embedding boyutu: {len(embedding)}")
                print(f"📊 İlk 5 değer: {embedding[:5]}")
            elif "embedding" in res_obj:
                embedding = res_obj["embedding"]
                print(f"✅ Başarılı! Embedding boyutu: {len(embedding)}")
                print(f"📊 İlk 5 değer: {embedding[:5]}")
            else:
                print("⚠️ Yanıt alındı ama embedding bulunamadı:")
                print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"❌ Hata oluştu: {e}")

if __name__ == "__main__":
    target_img = DEFAULT_IMAGE
    if len(sys.argv) > 1:
        target_img = sys.argv[1]
    
    print(f"🚀 Jina v5 Omni Embedding Testi Başlatılıyor... (Hedef: {target_img})")
    test_embedding_v2(target_img)
