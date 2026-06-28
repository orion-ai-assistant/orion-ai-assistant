Vertex AI icin Docker akisi bu projede ADC (Application Default Credentials) uzerinden ilerler.

Gerekli env degerleri:

```env
GOOGLE_CLOUD_PROJECT=gen-lang-client-0430976171
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS_HOST=C:/Users/<sen>/AppData/Roaming/gcloud/application_default_credentials.json
```

Lokal kurulum:

```bash
gcloud auth application-default login
docker compose -f docker-compose.dev.yml up -d --build
```

Notlar:

- Container icinde credential yolu sabit olarak `/app/gcp/application_default_credentials.json` kullanilir.
- `GOOGLE_CLOUD_PROJECT` bos birakilirsa agent startup sirasinda hata verir.
- GCP uzerinde deploy ediyorsan uzun omurlu JSON yerine attached service account tercih et.
