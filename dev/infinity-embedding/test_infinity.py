import requests
import base64

# Kullanıcının orijinal yönergesindeki gibi /embeddings olarak güncellendi.
API_URL = "http://localhost:8081/embeddings"

def url_to_base64(url, modality="image"):
    """İnternetteki bir resmi veya sesi Base64 formatına çeviren yardımcı fonksiyon."""
    print(f"  -> Dosya indiriliyor: {url}")
    response = requests.get(url)
    response.raise_for_status()
    base64_encoded = base64.b64encode(response.content).decode('utf-8')
    # Basit bir mimetype tahmini
    ext = url.split('.')[-1]
    mimetype = f"{modality}/{ext}"
    return f"data:{mimetype};base64,{base64_encoded}"

def test_infinity_endpoint(test_name, model_name, input_data, modality):
    print("\n" + "="*50)
    print(f"TEST: {test_name}")
    print(f"Model: {model_name} | Modality: {modality}")
    print("-" * 50)

    payload = {
        "model": model_name,
        "input": input_data,
        "modality": modality
    }

    try:
        # İsteği atıyoruz
        response = requests.post(API_URL, json=payload, timeout=20)
        
        # Eğer sunucu 200 (Başarılı) dışında bir kod dönerse (örn: 400, 422, 500) HTTPError fırlatır
        response.raise_for_status() 
        
        # Başarılı senaryo
        result = response.json()
        vector_length = len(result['data'][0]['embedding'])
        print(f"OK: {modality.capitalize()} basariyla vektorlestirildi.")
        print(f"Boyut: {vector_length}")
        print(f"Kullanim: {result.get('usage', {}).get('total_tokens', 'Bilinmiyor')} token.")
        
    except requests.exceptions.HTTPError:
        # Infinity'nin döndürdüğü o özel hata mesajlarını (örneğin "modality desteklenmiyor") yakalıyoruz
        print(f"HATA YAKALANDI: Sunucu istegi reddetti! (HTTP {response.status_code})")
        try:
            error_details = response.json()
            print(f"Infinity Hata Geri Donusu:\n   {error_details}")
        except:
            print(f"Ham Hata Mesaji:\n   {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("BAGLANTI HATASI: Sunucuya ulasilamiyor. Infinity acik mi? Port (8081) dogru mu?")
    except Exception as e:
        print(f"BEKLENMEYEN BIR DURUM OLUSTU: {e}")

if __name__ == "__main__":
    print("Infinity Embedding Servisi Test Ediliyor...\n")

    # TEST 1: Normal Metin (Başarılı olması beklenir)
    test_infinity_endpoint(
        test_name="Metin Testi (Text)",
        model_name="jina-v2",
        input_data=["Orion asistanı için çoklu modalite testi yapıyoruz."],
        modality="text"
    )

    # TEST 2: Görüntü Testi (Başarılı olması beklenir)
    image_url = "http://images.cocodataset.org/val2017/000000039769.jpg"
    try:
        b64_image = url_to_base64(image_url, "image")
        test_infinity_endpoint(
            test_name="Görüntü Testi (Image)",
            model_name="jina-v2",
            input_data=[b64_image],
            modality="image"
        )
    except Exception as e:
        print(f"Resim indirme hatası: {e}")

    # TEST 3: Ses Testi - YANLIŞ MODEL İLE (HATA Fırlatması beklenir!)
    audio_url = "https://github.com/michaelfeil/infinity/raw/main/libs/infinity_emb/tests/data/audio/beep.wav"
    try:
        b64_audio = url_to_base64(audio_url, "audio")
        test_infinity_endpoint(
            test_name="Ses Testi - Uyumsuz Model (HATA BEKLENİYOR)",
            model_name="jina-v2",
            input_data=[b64_audio],
            modality="audio"
        )
    except Exception as e:
        print(f"Ses indirme hatası: {e}")
