import json
import urllib.request
import math
import time

# Ayarlar
URL_OPENAI_EMBEDDINGS = "http://localhost:8081/v1/embeddings"
URL_LLAMA_EMBEDDINGS = "http://localhost:8081/embeddings"

def get_embedding(text, is_query=False):
    """Metin için embedding vektörü alır. Jina v5 için Asimetrik Prefix ekler."""
    
    # Jina v5 Asimetrik Arama Kuralı: Sorulara Query, Belgelere Document ön eki.
    if is_query:
        prefixed_text = f"Query: {text}"
    else:
        prefixed_text = f"Document: {text}"
        
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
            if isinstance(result, dict) and "data" in result and len(result["data"]) > 0:
                emb = result["data"][0]["embedding"]
            elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                emb = result[0]["embedding"]
            
            if emb:
                if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                    return emb[0]
                return emb
    except Exception as e:
        # OpenAI endpoint başarısız olursa Llama.cpp direkt endpoint'ine düş
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
                if isinstance(result, dict) and "embedding" in result:
                    emb = result["embedding"]
                elif isinstance(result, list) and len(result) > 0 and "embedding" in result[0]:
                    emb = result[0]["embedding"]
                
                if emb:
                    if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                        return emb[0]
                    return emb
        except Exception as e2:
            print(f"Hata: '{prefixed_text}' icin embedding alinamadi. ({e2})")
    
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
    start_time_total = time.time()
    print("=== Llama-CPP Hafıza/Retrieval Testi (Jina v5 Prefix Kuralı ile) ===\n")
    
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
        "Sisteme aşırı yüklenme olduğu için bilgisayarın fanı çok ses çıkardı.",

        # --- YENİ EKLENENLER ---

        # TEST GRUBU 4: Negatiflik / Zıtlık İşleme (Sadece bir ek/kelime değişiyor)
        "Sunucudaki logları inceledim ve bellek sızıntısı problemini çözdüm.",
        "Sunucudaki logları inceledim ama bellek sızıntısı problemini çözemedim.",
        "Bu modelin vektör uzayındaki başarısı kesinlikle tartışılmaz.",
        "Bu modelin vektör uzayındaki başarısı kesinlikle tartışılır.",

        # TEST GRUBU 5: Bağlamın en sonda değişmesi (Özellikle 'last' pooling testi için)
        "Saatlerce süren toplantının ardından aldığımız tek karar, hiçbir şey yapmamaktı.",
        "Saatlerce süren toplantının ardından aldığımız tek karar, projeyi hızlandırmaktı.",
        "Bütün gün sokaklarda yürüdükten sonra karşıma çıkan şey kocaman bir köpekti.",
        "Bütün gün sokaklarda yürüdükten sonra karşıma çıkan şey kocaman bir yalandı.",

        # TEST GRUBU 6: Leksikal Örtüşme (Aynı kelimeler, tamamen farklı konular)
        "Elma, sağlığa oldukça faydalı bir meyvedir.",
        "Apple, yeni duyurduğu cihazla teknoloji dünyasını salladı.",
        "Yüz kere söyledim sana o dosyayı silme diye!",
        "Dün havuzda tam yüz metre yüzdüm.",
        "Yüzümdeki yara izi giderek iyileşiyor.",

        # TEST GRUBU 7: Soru vs İfade (Soru işareti embedding'i nasıl etkiliyor?)
        "Docker compose ile postgresql ve redis konteynerlerini ayağa kaldırdın mı?",
        "Docker compose ile postgresql ve redis konteynerlerini ayağa kaldırdım.",
        "Local LLM için llama.cpp mi yoksa vLLM mi daha performanslı çalışıyor?",
        "Local LLM için llama.cpp, vLLM'den daha performanslı çalışıyor.",

        # TEST GRUBU 8: Domain Kümelemesi 1 - Yazılım & Altyapı
        "FastAPI ile yazılmış stateless API gateway, Redis pub/sub üzerinden workerlara job iletiyor.",
        "GGUF formatındaki modeller, düşük VRAM'e sahip cihazlarda bile çalışabiliyor.",
        "Embedding modellerinde context window aşıldığında model başlardaki bilgiyi unutabilir.",
        "Uygulamanın Docker volume yollarını Linux ve Windows'ta sorunsuz çalışacak şekilde tek bir formatta ayarladım.",

        # TEST GRUBU 9: Domain Kümelemesi 2 - Donanım & IoT
        "ESP32-C3 SuperMini kullanarak batarya beslemeli bir LED standı tasarlıyorum.",
        "Lityum pili güvenli şarj etmek için devreye TP4056 modülü entegre edildi.",
        "Devre akımını tamamen kesmek için dokunmatik sensör yerine fiziksel bir anahtar kullandım.",
        "Kapasitif dokunmatik sensörler bazen yanlış tetiklenmelere yol açabiliyor.",

        # TEST GRUBU 10: Domain Kümelemesi 3 - Mekanik & Araçlar
        "Motosikletin vites göstergesi bazen takılı kalıyor ve yanlış vitesi gösteriyor.",
        "125cc bir motorun şehir içi yakıt tüketimi oldukça avantajlıdır.",
        "Zincir yağlama ve gerginlik ayarı, sürüş güvenliği için düzenli yapılmalıdır.",
        "Kask takmadan yola çıkmak yapılabilecek en büyük hatadır.",

        # TEST GRUBU 11: Uzun Metin / Bilgi Seyrelmesi (Uzun cümlelerde ana fikir kayboluyor mu?)
        "Dün sabah erkenden kalkıp kahvemi içtikten sonra bilgisayarın başına geçtim, e-postalarımı kontrol ettim, birkaç haber okudum ve en sonunda günlerdir aradığım o kritik API anahtarını bulduğumu fark ettim.",
        "Yapay zeka modellerinin gelişimi her geçen gün hızlanırken, donanım gereksinimleri de aynı oranda artıyor ve bu durum bağımsız geliştiricilerin kendi evlerindeki bilgisayarlarda yüksek boyutlu modelleri çalıştırmasını giderek daha da imkansız hale getiriyor.",

        # TEST GRUBU 12: Anlamsız / Gürültü Cümleler (Sentaktik olarak doğru ama semantik olarak boş)
        "Mavi düşünceler fırında yavaşça kaynarken, duvarlar sessizce şarkı söylüyordu.",
        "Klavye tuşları gökyüzüne uçup bulutlarla dans etmeye başladığında saat gece yarısıydı.",
        "Saydam çilekler, geometrik rüzgarların fısıltısıyla üçgen şeklinde gülümsedi.",
        "Zamanın ağırlığı, plastik çatalın felsefik çöküşüyle senkronize bir şekilde eridi.",

        # TEST GRUBU 13: Tamamen Rastgele / Gibberish (Karakter dizileri ve anlamsız kelime grupları)
        "asdasd qwerty 12345 test test test",
        "xyz pqr abc 987 654",
        "lorem ipsum dolor sit amet consectetur adipiscing elit",
        "embed_test_v2_final_final_gercek.py",
        "NULL NaN undefined Error 404",

        # TEST GRUBU 14: Çok Kısa Konseptler (Model tek kelimeye / iki kelimeye nasıl tepki veriyor?)
        "Yapay Zeka",
        "Yapay zeka asistanı.",
        "Kırmızı.",
        "Elma.",
        "Motor bloğu.",
        "Veritabanı hatası."
    ]
    
    # 2. Sorular
    queries = [
        "Paramı ve kartlarımı koyduğum eşyaya ne oldu?", # İçinde cüzdan kelimesi bile yok
        "Sunucu neden hizmet veremez hale geldi?",       # İçinde veritabanı veya çökme kelimesi yok
        "İşi bırakma sebebim neydi?"                     # İstifa kelimesi yok, bağlam aranıyor
    ]
    
    for query in queries:
        start_time_query = time.time()
        print(f"\n>>> Soru: '{query}'")
        print("-" * 60)
        
        # Soru için query embedding'i al (is_query=True yapıyoruz)
        query_emb = get_embedding(query, is_query=True)
        
        if not query_emb:
            print("Soru için embedding alınamadı.")
            continue

        results = []
        for fact in facts:
            # Veritabanındaki bilgiler için fact embedding'i al (is_query=False yapıyoruz)
            fact_emb = get_embedding(fact, is_query=False)
            if fact_emb:
                sim = cosine_similarity(query_emb, fact_emb)
                results.append((fact, sim))
        
        # Sonuçları benzerliğe göre sırala
        results.sort(key=lambda x: x[1], reverse=True)
        
        # Çubuk grafiği (Eksi değerleri 0'a çekerek grafik hatasını önledim)
        for fact, sim in results[:8]:
            adjusted_sim = max(sim, 0)
            bar_len = int(adjusted_sim * 30)
            bar = "#" * bar_len + "-" * (30 - bar_len)
            print(f"[{bar}] {sim:.4f} -> {fact}")
        
        end_time_query = time.time()
        print(f"En Yakın Eşleşme: {results[0][0]}")
        print(f"[*] İşlem süresi: {end_time_query - start_time_query:.2f} saniye")

    end_time_total = time.time()
    print(f"\n[✓] Toplam Test Süresi: {end_time_total - start_time_total:.2f} saniye")

if __name__ == "__main__":
    run_test()