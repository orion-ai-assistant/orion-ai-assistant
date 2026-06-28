# Llama-Omni Embedding Test Rehberi

Bu dizin, `llama-omni-embedding` (Jina v5 Omni vb.) servislerini test etmek için gereken araçları ve örnek dosyaları içerir.

## Dosya İçeriği

- `embedding_omni_test.py`: Servise istek atan ana test scripti.
- `img.png`: Görsel embedding testleri için örnek resim.
- `auido.wav`: Ses embedding testleri için örnek ses dosyası.

## Kullanım

Scripti çalıştırmadan önce terminalinizde bu dizine (`dev/llama-omni-embedding`) gidin.

### 1. Sadece Metin Testi (Simple Style)

Servisin çalışıp çalışmadığını kontrol etmek için en temel yöntemdir.

```bash
python embedding_omni_test.py --base-url http://127.0.0.1:8081 --text "Merhaba dünya"
```

### 2. Multimodal Test (Metin + Resim + Ses)

Tüm modaliteleri aynı anda test etmek için `--payload-style multimodal` parametresini kullanın.

```bash
python embedding_omni_test.py --base-url http://127.0.0.1:8081 --payload-style multimodal --text "Bu bir testtir" --image img.png --audio auido.wav
```

### Parametreler

- `--base-url`: Servisin çalıştığı adres (Varsayılan: `http://127.0.0.1:8081`).
- `--payload-style`: `simple` (varsayılan) veya `multimodal`.
- `--text`: Gönderilecek metin içeriği.
- `--image`: Test edilecek resim dosyasının yolu.
- `--audio`: Test edilecek ses dosyasının yolu.
- `--model`: (Opsiyonel) Servis belirli bir model adı bekliyorsa buraya yazabilirsiniz.

## Dikkat Edilmesi Gerekenler

- **Port:** Eğer servisi farklı bir portta (örneğin 8080) başlattıysanız `--base-url http://127.0.0.1:8080` şeklinde güncelleyin.
- **Base64:** Script, resim ve ses dosyalarını otomatik olarak Base64 formatına çevirip JSON içinde gönderir.
- **Hata Mesajları:** Servisten dönen hatalar (404, 500 vb.) terminale yazdırılacaktır.
