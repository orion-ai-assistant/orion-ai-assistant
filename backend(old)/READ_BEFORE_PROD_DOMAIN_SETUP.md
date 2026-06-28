# 🚀 Orion: Production, Domain & Host Setup Guide

Bu dosya, Orion AI projesinin yerel geliştirmeden (Localhost) gerçek sunucu (Production) ortamına taşınması sırasında yapılacak kritik yapılandırmaları içerir.

## 🏗️ Büyük Resim: Trafik Nasıl Akacak?

"Madem domain aldım, neden hala kodda port yazıyoruz?" sorusunun cevabı burada. Domain binanın adresi, port ise daire numarasıdır.



1.  **Kullanıcı:** Tarayıcıya `https://api.orionai.com` yazar (Varsayılan olarak **443** portuna gider).
2.  **Cloudflare:** İsteği karşılar, güvenliği sağlar ve senin sunucuna (IP) yönlendirir.
3.  **Nginx (Kapıcı):** 443'ten gelen isteği yakalar ve içeride sessizce **8000** portunda çalışan FastAPI'ye paslar.
4.  **FastAPI:** İşi yapar ve cevabı Nginx üzerinden geri gönderir.

---

## 🛠️ Sunucu (Production) İçin `.env` Şablonu

Sunucuyu kiraladığında `.env` dosyanı bu mantıkla güncelle. Portlar yine var ama dış dünya bunları asla doğrudan görmeyecek.

```env
# --- SERVER LISTENING (İç Bağlantı) ---
# Uvicorn'un sunucu içinde hangi kapıyı çalacağı
API_HOST=0.0.0.0
API_PORT=8000

# --- PUBLIC URLS (Dış Bağlantı) ---
# Admin panelin ve kullanıcıların backend'e ulaşacağı gerçek adresler
API_BACKEND_URL=https://api.orionai.com
ADMIN_URL=https://admin.orionai.com

# --- CORS SETTINGS ---
# Sadece bu adreslerden gelen isteklere izin ver (Güvenlik!)
ALLOWED_ORIGINS=https://www.orionai.com,https://admin.orionai.com
```

---

## 🔧 Nginx "Tercümanlık" (Reverse Proxy) Ayarı

Nginx'e "Domaini portla birleştir" komutunu sunucu içindeki `/etc/nginx/sites-available/orion` dosyasında vereceksin:

```nginx
server {
    server_name api.orionai.com;

    location / {
        proxy_pass http://localhost:8000; # FastAPI'nin çalıştığı port
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Orion AI WebSocket Desteği (Streaming için Kritik!)
    location /ws {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
    }
}
```

---

## 🛰️ Adım Adım Host Ekleme Kontrol Listesi

- [ ] **Host Kiralama:** Ubuntu tabanlı bir VPS (Hetzner, DigitalOcean vb.) edin.
- [ ] **DNS Yönlendirme:** Cloudflare üzerinden domainini (A Record) sunucu IP'sine yönlendir.
- [ ] **Python Ortamı:** Sunucuda `venv` oluştur ve `requirements.txt`'i yükle.
- [ ] **Port Güvenliği:** Sunucu firewall'undan (UFW) `8000` portunu dışarıya kapat, sadece `80` ve `443` portlarını açık tut.
- [ ] **SSL Aktif Et:** Cloudflare "SSL/TLS" sekmesinden "Full" veya "Strict" modunu seç.

---

## 💡 Neden Hala Port Yazıyoruz? (Özet)

* **Lokalde:** Nginx olmadığı için tarayıcıya direkt `localhost:8000` yazarak o kapıyı kendin çalıyorsun.
* **Sunucuda:** Kapıda Nginx (Reverse Proxy) var. Sen domaini çalıyorsun, Nginx senin yerine içerideki `8000` numaralı kapıyı çalıyor.

---

**⚠️ Kritik Not:** Eğer `API_BACKEND_URL`'i yanlış yazarsan admin panelin backend ile konuşamaz. Her zaman `https://` kullandığından emin ol!

