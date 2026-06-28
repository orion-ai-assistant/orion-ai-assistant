import json
import urllib.request
import math

# Ayarlar
URL_OPENAI_EMBEDDINGS = "http://localhost:8081/v1/embeddings"
URL_LLAMA_EMBEDDINGS = "http://localhost:8081/embeddings"

def get_embedding(text):
    """Metin için embedding vektörü alır."""
    payload = {"input": text, "model": "embedding"}
    req = urllib.request.Request(
        URL_OPENAI_EMBEDDINGS,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8")
            result = json.loads(data)
            
            emb = None
            if isinstance(result, dict) and "data" in result and len(result["data"]) > 0:
                emb = result["data"][0]["embedding"]
            elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                emb = result[0]["embedding"]
            
            if emb:
                if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                    return emb[0]
                return emb
    except Exception as e:
        try:
            payload_llama = {"content": text}
            req_llama = urllib.request.Request(
                URL_LLAMA_EMBEDDINGS,
                data=json.dumps(payload_llama).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req_llama, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                result = json.loads(data)
                
                emb = None
                if isinstance(result, dict) and "embedding" in result:
                    emb = result["embedding"]
                elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                    emb = result[0]["embedding"]
                
                if emb:
                    if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                        return emb[0]
                    return emb
        except Exception as e2:
            print(f"Hata: '{text}' icin embedding alinamadi. ({e2})")
    
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
    print("=== Llama-CPP Hafıza Kelime Konumu ve Bağlam Testi ===\n")
    
    # 1. Tuzaklı Kayıtlı Bilgiler (Kilit kelimelerin yerleri değiştirilmiş)
    facts = [
        # TEST GRUBU 1: Kilit kelime (Cüzdan/Çalındı) nerede?
        "Cüzdanımı dün gece eve dönerken karanlık sokakta düşürdüm.",           # Olay başta, önemsiz detay sonda
        "Dün gece eve dönerken karanlık sokakta düşürdüğüm şey cüzdanımdı.",    # Olay sonda, önemsiz detay başta
        
        # TEST GRUBU 2: Kilit bilgi (Veritabanı/Çöktü) nerede?
        "Veritabanı sunucusu, sisteme aşırı yüklenme olduğu için tamamen çöktü.", # Bilgi başta, sebep sonda
        "Sisteme aşırı yüklenme olduğu için tamamen çöken şey veritabanı sunucusuydu.", # Sebep başta, bilgi sonda
        
        # TEST GRUBU 3: Aynı kelimeler, zıt anlam! (Model sadece kelimeye mi bakıyor, anlama mı?)
        "Patronum bana bağırdığı için sinirlenip işten istifa ettim.", # İstifanın sebebi patron
        "Ben sinirlenip işten istifa ettiğim için patronum bana bağırdı.", # Bağırmanın sebebi istifa
        
        # Tuzak/Alakasız cümleler (Gürültü)
        "Dün gece eve dönerken karanlık sokakta çok üşüdüm.",
        "Sisteme aşırı yüklenme olduğu için bilgisayarın fanı çok ses çıkardı."
    ]
    
    # 2. Sorular
    queries = [
        "Paramı ve kartlarımı koyduğum eşyaya ne oldu?", # İçinde cüzdan kelimesi bile yok
        "Sunucu neden hizmet veremez hale geldi?",       # İçinde veritabanı veya çökme kelimesi yok
        "İşi bırakma sebebim neydi?"                     # İstifa kelimesi yok, bağlam aranıyor
    ]
    
    for query in queries:
        print(f"\n>>> Soru: '{query}'")
        print("-" * 60)
        
        query_emb = get_embedding(query)
        if not query_emb:
            continue

        results = []
        for fact in facts:
            fact_emb = get_embedding(fact)
            if fact_emb:
                sim = cosine_similarity(query_emb, fact_emb)
                results.append((fact, sim))
        
        results.sort(key=lambda x: x[1], reverse=True)
        
        for fact, sim in results:
            bar_len = int((sim if sim > 0 else 0) * 30)
            bar = "#" * bar_len + "-" * (30 - bar_len)
            print(f"[{bar}] {sim:.4f} -> {fact}")
        
        print(f"En Yakın Eşleşme: {results[0][0]}")

if __name__ == "__main__":
    run_test()