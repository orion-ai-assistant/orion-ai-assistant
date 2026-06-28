# 🚀 Infinity Embedding Test Rehberi

Bu klasör, Orion AI projesindeki **Infinity Embedding Server** servisini doğrulamak ve yeteneklerini (metin, resim, ses) test etmek için tasarlanmış araçları içerir.

## 🛠️ Ön Gereksinimler

Test scripti `requests` kütüphanesini kullanır. Eğer yüklü değilse şu komutla yükleyebilirsin:

```bash
pip install requests
```

## 🏃‍♂️ Testi Çalıştırma

Terminalini aç ve `dev/embedding` dizinine giderek şu komutu çalıştır:

```bash
python dev\embedding\test_infinity.py
```

## 📋 Test Senaryoları

Script üç ana aşamadan geçer:

1.  **Metin Testi (Text):** `jina-v2` modeline standart bir cümle gönderir. Başarılı bir vektör dönmesi beklenir.
2.  **Görüntü Testi (Image):** İnternet üzerindeki bir resmi indirir, **Base64** formatına çevirir ve vektörleştirir. Çoklu modalite desteğini doğrular.
3.  **Hata Yakalama (Audio):** `jina-v2` (sadece metin/resim destekler) modeline **ses** verisi gönderir. Burada sistemin çökmemesi ve Infinity'nin `400` hata koduyla "bu model sesi desteklemiyor" demesi beklenir.

## 🔍 Sonuçları Anlama

-   **✅ BAŞARILI:** Servis doğru çalışıyor ve vektör boyutları modelin mimarisine uygun (örn. Jina için 768 veya 1024).
-   **❌ HATA YAKALANDI:** Sunucu isteği reddettiğinde (bilinçli yapılan hata testleri dahil) Infinity'den gelen detaylı JSON mesajı ekrana yazılır.
-   **❌ BAĞLANTI HATASI:** Eğer bu hatayı alıyorsan Docker üzerinde `embed-server` konteynerinin çalışıp çalışmadığını ve `8081` portunun açık olduğunu kontrol et.

---
> **Not:** Bu testler geliştirme aşamasında "Happy Path" (sorunsuz yol) ve "Edge Case" (sınır durumları) senaryolarını hızlıca denemek içindir.
