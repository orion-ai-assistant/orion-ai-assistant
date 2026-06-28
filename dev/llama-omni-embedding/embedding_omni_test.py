import base64
import json
import urllib.request
import os

def final_scan(image_path):
    url = "http://127.0.0.1:8081/embedding"
    if not os.path.exists(image_path): return print("Resim yok!")
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")

    # Jina EuroBERT ve Qwen3-VL mimarisinde asıl tetikleyiciler:
    # 128005: Image Pad, 128004: Belki gizli kanca
    candidate_ids = [128005, 128004, 128257, 128258, 2, 3]

    print("🔍 Orion 'Deep Vector' Taraması Başlıyor...")
    print("Fiziksel Batch limitini (ub) 1024+ yaptıysan sonuç alacağız.\n")

    for tid in candidate_ids:
        payload = {
            "content": [tid], 
            "image_data": [{"data": img_b64, "id": 0}]
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                # LİSTE HATASINI BURADA ÇÖZÜYORUZ:
                data = result[0] if isinstance(result, list) else result
                
                tokens = data.get("tokens_evaluated", 0)
                emb = data.get("embedding", [])

                if tokens > 50:
                    print("==================================================")
                    print(f"🔥 ZAFER! ÇALIŞAN MARKER ID: {tid}")
                    print(f"✅ Token Sayısı: {tokens} (Resim başarıyla açıldı!)")
                    print(f"✅ Vektör Boyutu: {len(emb)}")
                    print("==================================================")
                    return
                else:
                    print(f"  [ID {tid}] 200 OK ama resim tetiklenmedi (Tokens: {tokens})")

        except Exception as e:
            # 0 markers hatası gelirse sessizce geç
            if "number of markers" not in str(e):
                print(f"  [ID {tid}] Hata: {e}")

    print("\n🚨 Hala 1 token alıyorsan; sunucu başlangıcında '-ub 2048' olduğundan emin ol.")

if __name__ == "__main__":
    final_scan("img.png")