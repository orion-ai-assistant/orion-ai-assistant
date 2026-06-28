# Orion AI Backend — Çalıştırma ve Test Kılavuzu

Bu doküman, projenin hem **sunucu (FastAPI)** hem de **CLI (terminal test arayüzü)** olarak nasıl çalıştırılacağını açıklar.

---

## 1. Ortamı Hazırlama

Önce sanal ortamı (virtualenv) aktive et:

```sh
# Windows PowerShell
.venv\Scripts\Activate.ps1

# veya klasik cmd
.venv\Scripts\activate.bat
```

---

## 2. Sunucuyu (FastAPI) Başlatmak

**Ana API sunucusunu başlatmak için:**

```sh
cd backend
python -m api.app.main
```

- Sunucu `0.0.0.0:8000` (veya `.env`/ortam değişkenine göre) üzerinde çalışır.
- WebSocket endpoint'i: `/ws`
- HTTP endpoint'leri: `/api/*`, `/mock/*` vs.

> **Not:**
> Çalışma dizininiz mutlaka `backend` kökü olmalı! (Aksi halde import hatası alırsınız.)

---

## 3. Agent'ı CLI (Terminal) ile Test Etmek

Agent'ı doğrudan terminalden test etmek için:

```sh
cd backend
python -m agent.cli
```

- Bu komut, terminalde etkileşimli bir şekilde agent ile konuşmanızı sağlar.
- Model değiştirmek için: `model openai` veya `model gemini`
- JSON debug modu: `debug`
- Çıkmak için: `quit` veya `exit`

> **Not:**
> `python agent/cli.py` ile çalışmaz! Mutlaka `python -m agent.cli` kullanın.

---

## 4. Sık Karşılaşılan Hatalar ve Çözümleri

- **ModuleNotFoundError: No module named 'agent'**
  - Çalışma dizininiz yanlış. `cd backend` yapıp tekrar deneyin.
- **ModuleNotFoundError: No module named 'settings'**
  - Yine çalışma dizini veya modül başlatma şekli hatalı.
- **Çözüm:** Her zaman `python -m ...` ile başlatın ve kök dizinde olun.

---

## 5. Ekstra: Ortam Değişkenleri

- `.env` dosyasındaki değişkenler otomatik yüklenir.
- Model seçimi için: `AGENT_MODEL=openai` veya `AGENT_MODEL=gemini`
- WebSocket için: `WS_ENABLE_REAL_AGENT=true` (mock yerine gerçek agent)

---

Herhangi bir sorunda bu dosyadaki adımları tekrar kontrol edin!
