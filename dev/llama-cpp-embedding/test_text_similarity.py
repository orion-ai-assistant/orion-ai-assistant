import json
import urllib.request
import math
import sys

# Ayarlar
URL_LLAMA_EMBEDDINGS = "http://localhost:8081/embeddings"
URL_OPENAI_EMBEDDINGS = "http://localhost:8081/v1/embeddings"

def get_embedding(text):
    """Metin için embedding vektörü alır."""
    last_error = "Bilinmeyen hata"
    # Sunucunun destekleyebileceği muhtemel endpoint'ler
    endpoints = [
        (URL_OPENAI_EMBEDDINGS, {"input": text, "model": "embedding"}),
        (URL_LLAMA_EMBEDDINGS, {"content": text}),
        ("http://localhost:8081/embedding", {"content": text})
    ]
    
    for url, payload in endpoints:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("utf-8")
                result = json.loads(data)
                
                # OpenAI formati: {'data': [{'embedding': [...]}]}
                if isinstance(result, dict) and "data" in result and len(result["data"]) > 0:
                    emb = result["data"][0]["embedding"]
                # Llama.cpp direkt format: {'embedding': [...]}
                elif isinstance(result, dict) and "embedding" in result:
                    emb = result["embedding"]
                # Liste formatı: [{'embedding': [...]}]
                elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                    emb = result[0]["embedding"]
                
                if emb:
                    # Robustluk: Eğer liste içinde liste döndüyse içtekini al
                    if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                        return emb[0]
                    return emb
        except Exception as e:
            last_error = str(e)
            continue # Diger endpoint'i dene
            
    print(f"Hata: '{text}' icin embedding alinamadi. (Son hata: {last_error})")
    return None

def cosine_similarity(v1, v2):
    """İki vektör arasındaki kosinüs benzerliğini hesaplar."""
    if not v1 or not v2: return 0.0
    
    dot_product = sum(a * b for a, b in zip(v1, v2))
    magnitude1 = math.sqrt(sum(a * a for a in v1))
    magnitude2 = math.sqrt(sum(b * b for b in v2))
    
    if magnitude1 == 0 or magnitude2 == 0:
        return 0.0
    return dot_product / (magnitude1 * magnitude2)

def test_similarity(text1, text2):
    print(f"--- Karsilastiriliyor ---")
    print(f"   1: '{text1}'")
    print(f"   2: '{text2}'")
    
    emb1 = get_embedding(text1)
    emb2 = get_embedding(text2)
    
    if emb1 and emb2:
        sim = cosine_similarity(emb1, emb2)
        print(f"Sonuc: Benzerlik Skoru: {sim:.4f}")
        
        # Gorsellestirme
        bar_len = int(sim * 20)
        bar = "#" * bar_len + "-" * (20 - bar_len)
        print(f"Grafik: [{bar}] %{sim*100:.2f}")
    else:
        print("Hata: Embedding'ler alinamadi.")
    print("-" * 40)

if __name__ == "__main__":
    print("Llama-CPP Text Embedding Testi Baslatiliyor...")
    print(f"Endpoints: {URL_LLAMA_EMBEDDINGS}, {URL_OPENAI_EMBEDDINGS}\n")
    
    # Gercek Dunya Senaryolari
    # === STRES TESTLERİ ===
    print("=== MODEL STRES TESTLERİ ===\n")
    
    # 1. Eş Anlamlılık (Synonym) - Matching Modu
    test_similarity("Otomobilin motorundaki arizayi gidermek icin servise gittim.", 
                    "Aractaki teknik problemi cozmek adina tamirhaneye ugradim.", is_rag=False)

    # 2. Çok Anlamlılık (Polysemy) - "Banka" kelimesi
    print("--- Cok Anlamlilik (Banka) ---")
    emb_query = get_embedding("Bankaya gidip biraz para cekmem gerekiyor.")
    emb_target1 = get_embedding("Finans kuruluslari kredi faizlerini guncelledi.") # Finansal
    emb_target2 = get_embedding("Nehrin kenarindaki bankta oturup manzarayı izledik.") # Mobilya
    
    sim1 = cosine_similarity(emb_query, emb_target1)
    sim2 = cosine_similarity(emb_query, emb_target2)
    print(f"   Q: 'Para cekmek icin banka...'")
    print(f"   V1 (Finans): {sim1:.4f} | V2 (Mobilya): {sim2:.4f}")
    print("   Sonuc: " + ("BASARILI" if sim1 > sim2 else "BASARISIZ") + " (Finansal anlam daha yakin olmali)")
    print("-" * 40)

    # 3. Olumsuzluk (Polarity)
    test_similarity("Bugun kendimi cok mutlu ve enerjik hissediyorum.", 
                    "Bugun hic mutlu degilim ve enerjim yok.")

    # 4. Soru-Cevap (Asymmetric Retrieval) - RAG Modu
    test_similarity("Kedi kumu nasil temizlenir?", 
                    "Evcil hayvan hijyeni icin topaklanan granullerin kurek yardimiyla gunluk olarak arindirilmasi ve eksilen miktarin tamamlanmasi onerilir.")

    # 5. Turkce-Ingilizce Gecisi (Cross-Lingual) - Matching Modu
    test_similarity("Yapay zeka gelecekte insan is gucunun yerini alabilir mi?", 
                    "Could artificial intelligence replace human labor in the future?")
