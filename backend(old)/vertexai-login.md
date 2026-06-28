Docker içinde Vertex AI için birkaç authentication yöntemi var:

## Seçenekler

**1. Service Account Key (En Kolay)**

Google Cloud Console'dan Service Account key oluştur:
```
IAM & Admin → Service Accounts → Key oluştur (JSON)
```

Dockerfile'a ekle:
```dockerfile
COPY service-account-key.json /app/service-account-key.json
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/service-account-key.json
```

Kod değişmez, olduğu gibi çalışır ✅

---

**2. docker run sırasında volume mount (Daha Güvenli)**

Key dosyasını image'a gömme, çalıştırırken ver:
```bash
docker run -v /path/to/key.json:/app/key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/key.json \
  your-image
```

---

**3. Mevcut gcloud credentials'ı mount et (Dev için)**

Localden credentials'ı Docker'a aktar:
```bash
docker run -v ~/.config/gcloud:/root/.config/gcloud \
  your-image
```

---

## Önerim

| Ortam | Yöntem |
|-------|--------|
| Development | gcloud credentials mount |
| Production | Service Account Key (env var ile) |
| GCP üzerindeyse (Cloud Run vs) | Workload Identity (key gerekmez) |

---

**Service Account oluştururken şu rolleri ver:**
- `Vertex AI User`
- `ML Engine Developer` (gerekirse)

Hangi ortam için kuruyorsun? (local dev mi, production mu?) Ona göre daha detaylı yardım edebilirim.