import base64
import json
import urllib.request
import os
import math

# Ayarlar
URL_EMBEDDINGS = "http://localhost:8081/embeddings"
IMG1 = "img.jpg"
IMG2 = "img2.png"

def get_embedding(image_path):
    if not os.path.exists(image_path):
        print(f"❌ Hata: {image_path} bulunamadı!")
        return None
    
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode('utf-8')

    # KRİTİK DENEME: Direkt Token ID Listesi Kullanımı
    # Sunucu loglarındaki "elements must be a string or a list of tokens" 
    # ifadesine dayanarak direkt token ID'leri gönderiyoruz.
    # 128000: BOS, 128005: Image Marker (Jina v5)
    tokens = [128000, 128005]
    
    payload = {
        "content": tokens, 
        "image_data": [{"data": img_b64, "id": 0}]
    }

    url = "http://localhost:8081/embeddings"
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            res_obj = result[0] if isinstance(result, list) else result
            
            data = res_obj.get("data", [])
            if data and isinstance(data, list): 
                emb = data[0].get("embedding")
            else:
                emb = res_obj.get("embedding")

            if emb and isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                emb = emb[0]
            return emb
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"❌ HTTP Hatası ({e.code}): {error_body}")
        return None
    except Exception as e:
        print(f"❌ Hata: {e}")
        return None

def cosine_similarity(v1, v2):
    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude1 = math.sqrt(sum(a * a for a in v1))
    magnitude2 = math.sqrt(sum(b * b for b in v2))
    if magnitude1 == 0 or magnitude2 == 0: return 0
    return dot_product / (magnitude1 * magnitude2)

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path1 = os.path.join(base_dir, IMG1)
    path2 = os.path.join(base_dir, IMG2)

    print("--- Karsilastirma Basliyor ---")
    
    emb1 = get_embedding(path1)
    emb2 = get_embedding(path2)

    if emb1 and emb2:
        print(f"\n[V1 İlk 3]: {emb1[:3]}...")
        print(f"[V2 İlk 3]: {emb2[:3]}...")
        
        similarity = cosine_similarity(emb1, emb2)
        percentage = similarity * 100
        
        print("\n" + "="*45)
        print(f"BENZERLIK SKORU: %{percentage:.2f}")
        print("="*45)
        
        if percentage > 99.999:
            print("KRITIK: Skor tam %100! Resimler islenmiyor.")
        elif percentage > 80:
            print("Sonuc: Benzer gorseller.")
        else:
            print("Sonuc: Farkli gorseller! (Basarili!)")
    else:
        print("Hata: Embeddingler alinamadi.")
