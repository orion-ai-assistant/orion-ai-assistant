import os
import json
import urllib.request
import math
from dotenv import load_dotenv

load_dotenv()

# Ayarlar
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
URL_OPENROUTER_EMBEDDINGS = "https://openrouter.ai/api/v1/embeddings"
MODEL_NAME = "nvidia/llama-nemotron-embed-vl-1b-v2:free"

def get_embedding(text):
    """OpenRouter üzerinden nvidia/llama-nemotron-embed-vl-1b-v2:free modeli ile embedding alır."""
    
    # Multimodal destekli model olduğu için bu formatı kullanıyoruz
    payload = {
        "model": MODEL_NAME,
        "input": [
            {
                "content": [
                    {"type": "text", "text": text}
                ]
            }
        ]
    }
    
    # Not: Eğer model standart formatı destekliyorsa payload = {"model": MODEL_NAME, "input": text} de çalışabilir.
    
    req = urllib.request.Request(
        URL_OPENROUTER_EMBEDDINGS,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://github.com/orion-ai-assistant", # Örnek
            "X-Title": "Orion AI Assistant Test"
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            result = json.loads(data)
            
            if "data" in result and len(result["data"]) > 0:
                emb = result["data"][0]["embedding"]
                return emb
            else:
                print(f"Hata: API'den geçersiz yanıt döndü: {result}")
    except Exception as e:
        print(f"Hata: '{text}' için embedding alınamadı. ({e})")
        if hasattr(e, 'read'):
            print(f"Hata Detayı: {e.read().decode('utf-8')}")
    
    return None

def cosine_similarity(v1, v2):
    """İki vektör arasındaki kosinüs benzerliğini hesaplar."""
    if not v1 or not v2: return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude1 = math.sqrt(sum(a * a for a in v1))
    magnitude2 = math.sqrt(sum(b * b for b in v2))
    if magnitude1 == 0 or magnitude2 == 0: return 0.0
    return dot_product / (magnitude1 * magnitude2)

def run_test():
    print(f"=== OpenRouter Retrieval Testi ({MODEL_NAME}) ===\n")
    
    if not OPENROUTER_API_KEY:
        print("!!! Hata: API KEY tanımlanmamış. Lütfen .env dosyasını kontrol edin.")
        return

    # 1. Kayıtlı Bilgiler (Knowledge Base)
    facts = [
        "Geçen hafta parka gittim ve çok eğlendim.",
        "Geçen hafta evde oturup bütün gün kitap okudum.",
        "Haftaya parka gitmeyi planlıyoruz.",
        "Park sorunu yüzünden arabayı caddenin çok uzağına bırakmak zorunda kaldım.",
        "Dün akşam mahalledeki çocuk parkının önünden geçtim ama girmedim.",
        "Bahçedeki tahta bankta oturup kahve içmek beni çok dinlendiriyor.",
        "Beşiktaş sahilinde uzun bir yürüyüş yaptım geçen pazar.",
        "En sevdiğim aktivite doğa parklarında kamp yapmaktır.",
        "Dün akşam çok lezzetli bir pizza yedim.",
        "Yarın sabah saat 10'da önemli bir toplantım var."
    ]
    
    # Sadece bir kez alıp saklayalım (Hız ve kota için)
    print("Bilgiler embed ediliyor...")
    fact_embeddings = []
    for fact in facts:
        emb = get_embedding(fact)
        if emb:
            fact_embeddings.append((fact, emb))
    
    if not fact_embeddings:
        print("Bilgiler için embedding alınamadı. Test sonlandırılıyor.")
        return

    # 2. Sorular
    queries = [
        "Geçen hafta nereye gitmiştim?",
        "Haftaya ne yapacağım?",
        "Araba ile ilgili ne sorun yaşadım?",
        "Hangi gün pizza yedim?",
        "Boş zamanlarımda ne okurum?",
        "Nerede yürüyüş yaptım?"
    ]
    
    for query in queries:
        print(f"\n>>> Soru: '{query}'")
        print("-" * 60)
        
        query_emb = get_embedding(query)
        
        if not query_emb:
            print("Soru için embedding alınamadı.")
            continue

        results = []
        for fact, fact_emb in fact_embeddings:
            sim = cosine_similarity(query_emb, fact_emb)
            results.append((fact, sim))
        
        # Sonuçları benzerliğe göre sırala
        results.sort(key=lambda x: x[1], reverse=True)
        
        for fact, sim in results[:5]:
            bar_len = int(sim * 30)
            bar = "#" * bar_len + "-" * (30 - bar_len)
            print(f"[{bar}] {sim:.4f} -> {fact}")
        
        print(f"En Yakın Eşleşme: {results[0][0]}")
    
if __name__ == "__main__":
    run_test()
