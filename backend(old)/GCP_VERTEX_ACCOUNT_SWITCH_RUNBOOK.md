# GCP Vertex AI Hesap Degistirme ve Hata Cozme Runbook

Bu dokuman, farkli Google hesabi ile tekrar test yaparken ayni sorunlari hizli cozmek icin hazirlandi.

## 1) Hedef Hesabi Aktif Et

```powershell
gcloud auth list
gcloud config set account oriontest26@gmail.com
```

## 2) Hedef Projeyi Sec (veya olustur)

Mevcut proje secmek icin:

```powershell
gcloud projects list --format="value(projectId,name)"
gcloud config set project PROJE_ID
```

Yeni proje olusturmak icin:

```powershell
gcloud projects create PROJE_ID --name="PROJE_ADI"
gcloud config set project PROJE_ID
```

Not: `PROJECT_ID` 6-30 karakter olmali, kucuk harf/rakam/tire disinda karakter olmamali.

## 3) Billing Bagla

```powershell
gcloud billing accounts list --format="json"
gcloud billing projects link PROJE_ID --billing-account BILLING_ACCOUNT_ID
```

Kontrol:

```powershell
gcloud billing projects describe PROJE_ID
```

## 4) Gerekli API'leri Ac

```powershell
gcloud services enable aiplatform.googleapis.com serviceusage.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com
```

Kontrol:

```powershell
gcloud services list --enabled --format="value(name)" | findstr "aiplatform.googleapis.com serviceusage.googleapis.com iam.googleapis.com cloudresourcemanager.googleapis.com"
```

## 5) En Kritik Nokta: ADC'yi Dogru Hesaba Al

`gcloud config set account` tek basina yetmez. Python SDK bazen eski ADC hesabi ile gider.

Eski ADC'yi temizle:

```powershell
gcloud auth application-default revoke --quiet
```

Yeni hesapla ADC login:

```powershell
gcloud auth application-default login oriontest26@gmail.com
```

Quota project yaz:

```powershell
gcloud auth application-default set-quota-project PROJE_ID
```

ADC hangi e-posta ile calisiyor kontrol et:

```powershell
$t = gcloud auth application-default print-access-token --quiet
Invoke-RestMethod -Uri ("https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=" + $t) | Format-List email
```

## 6) Bu Repoda Test Dosyalari Icin Onerilen Ayarlar

- `PROJECT_ID = "orionai-assistant-2604"`
- `MODEL = "gemini-2.5-flash"`
- Turkiye icin ilk deneme bolgesi: `LOCATION = "europe-west3"`
- Alternatif bolge: `LOCATION = "europe-west1"`

Not: Bolgeye gore model erisimi degisebilir. `404 model not found` alirsan model/bolge uyumu bozulmustur.

## 7) Sık Hatalar ve Cozum

### Hata: 403 BILLING_DISABLED
Anlam: Projede billing kapali.

Cozum:
1. Billing account bagla.
2. 1-5 dakika bekle.
3. Tekrar dene.

### Hata: 403 IAM_PERMISSION_DENIED (`aiplatform.endpoints.predict`)
Anlam: Cagri eski/yanlis kimlikle gidiyor veya hesapta yetki yok.

Cozum:
1. `gcloud auth list` ve `gcloud config get-value project` kontrol et.
2. ADC'yi revoke + login ile dogru hesaba al.
3. `set-quota-project` calistir.

### Hata: 404 Publisher Model not found
Anlam: Model o bolgede yok veya proje/model erisimi yok.

Cozum:
1. `gemini-2.5-flash` kullan.
2. `LOCATION` degistir (`europe-west3` / `europe-west1` / `us-central1`).

### Hata: 429 Resource exhausted
Anlam: Kota / hiz limiti / paylasimli kapasite siniri.

Cozum:
1. Istek hizini dusur (istekler arasina 0.5-1.5 sn koy).
2. Kisa sure bekleyip tekrar dene.
3. Daha az eszamanli istek gonder.
4. Gerekirse quota artisi talep et.

## 8) Neden 300 ms yerine 400-1200 ms gorunebiliyor?

Bu normaldir; TTFT sabit degildir.

Baslica sebepler:
1. Ag gecikmesi ve internet jitter.
2. Sunucu tarafi anlik yuk ve paylasimli kapasite.
3. Kota baskisi (429 oncesi gecikme artisi).
4. Ilk istek/soket/TLS maliyeti (warmup sonrasi bile dalgalanma olur).
5. Prompt uzunlugu ve cikis token sayisi.

Pratik beklenti (TR + `europe-west3` + kisa prompt):
- Iyi durumda: 250-450 ms
- Tipik: 400-800 ms
- Yogunlukta: 800+ ms veya 429

## 9) Hızli Kontrol Listesi

1. Hesap dogru mu?
2. Proje dogru mu?
3. Billing bagli mi?
4. API'ler acik mi?
5. ADC dogru hesaba mi ait?
6. Model + region uyumlu mu?
