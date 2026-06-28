import json
import urllib.request
import math

# Ayarlar
URL_OPENAI_EMBEDDINGS = "http://localhost:8081/v1/embeddings"
URL_LLAMA_EMBEDDINGS = "http://localhost:8081/embeddings"

def get_embedding(text):
    """Metin için embedding vektörü alır."""
    # LCO-Embedding modelinde prefix gerekmez
    prefixed_text = text
    
    payload = {"input": prefixed_text, "model": "embedding"}
    
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
            # OpenAI format: {'data': [{'embedding': [...]}]}
            if isinstance(result, dict) and "data" in result and len(result["data"]) > 0:
                emb = result["data"][0]["embedding"]
            # Some versions return list of dicts directly: [{'embedding': [...]}]
            elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                emb = result[0]["embedding"]
            
            if emb:
                # Eğer liste içinde liste döndüyse (örn: [[...]]), içtekini al
                if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                    return emb[0]
                return emb
    except Exception as e:
        # Fallback to llama.cpp direct endpoint if OpenAI compatible one fails
        try:
            payload_llama = {"content": prefixed_text}
            req_llama = urllib.request.Request(
                URL_LLAMA_EMBEDDINGS,
                data=json.dumps(payload_llama).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req_llama, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                result = json.loads(data)
                
                emb = None
                # Format: {'embedding': [...]}
                if isinstance(result, dict) and "embedding" in result:
                    emb = result["embedding"]
                # Format: [{'embedding': [...]}]
                elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                    emb = result[0]["embedding"]
                
                if emb:
                    # Eğer liste içinde liste döndüyse (örn: [[...]]), içtekini al
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
    print("=== Llama-CPP Hafıza/Retrieval Testi ===\n")
    
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
    
    # 2. Sorular (Farklı senaryoları test etmek için)
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
        
        # Soru için query embedding'i al
        query_emb = get_embedding(query)
        
        if not query_emb:
            print("Soru için embedding alınamadı.")
            continue

        results = []
        for fact in facts:
            fact_emb = get_embedding(fact)
            if fact_emb:
                sim = cosine_similarity(query_emb, fact_emb)
                results.append((fact, sim))
        
        # Sonuçları benzerliğe göre sırala
        results.sort(key=lambda x: x[1], reverse=True)
        
        # Daha fazla sonuç göstererek analizi kolaylaştır
        for fact, sim in results[:8]:
            bar_len = int(sim * 30)
            bar = "#" * bar_len + "-" * (30 - bar_len)
            print(f"[{bar}] {sim:.4f} -> {fact}")
        
        print(f"En Yakın Eşleşme: {results[0][0]}")
    
if __name__ == "__main__":
    run_test()
