Harika bir temel atmışsın! Projen oldukça modüler ve "tak-çıkar" mantığıyla ilerliyor. İstediğin analizi senin için daha düzenli, profesyonel ve **Türkçe** bir rehber haline getirdim.

Dosya yapılarını, görevlerini ve şu anki sağlık durumlarını (çalışıp çalışmadıklarını) adım adım inceleyelim:

---

## 📁 1. `backend/agent` – Yapay Zeka Beyni (Core Agent)

Bu klasör, asistanın "düşünme" ve "karar verme" mekanizmasını barındırıyor. LangGraph kullanılarak inşa edilmiş.

| Dosya | Görevi (Ne İşe Yarar?) | Durumu (Çalışıyor mu?) |
| --- | --- | --- |
| `__init__.py` | Klasörü bir paket haline getirir. Dışarıya `graph` ve `SYSTEM_PROMPT` gibi ana bileşenleri sunar. | ✅ **Aktif.** Sorunsuz. |
| `cli.py` | Asistanı terminalden test etmeni sağlar. Mesaj geçmişini tutar, spinner (yükleniyor simgesi) gösterir ve modeli anlık değiştirebilir. | ✅ **Aktif.** Manuel testler için hazır. |
| `graph.py` | **Ana İş Akışı:** LangGraph yapısını tanımlar. Modelin ne zaman konuşacağına, ne zaman araç (tool) kullanacağına karar veren "beyin" burasıdır. | ✅ **Aktif.** Akış (workflow) tamamlanmış. |
| `model.py` | LLM yapılandırması. Gemini veya OpenAI modellerini `.env` dosyasından okuyarak hazırlar. Sıcaklık (temperature) ayarlarını yapar. | ✅ **Aktif.** API anahtarları varsa çalışır. |
| `prompts.py` | Asistanın karakterini belirleyen sistem talimatlarını (System Prompt) içerir. | ✅ **Aktif.** |
| `tools.py` | **Yetenekler:** İnternette arama yapma (Tavily), saat bilgisi ve hava durumu gibi fonksiyonları içerir. | ⚠️ **Kısmen.** Tavily API anahtarı gerektirir. |
| `langgraph.json` | LangGraph CLI için yapılandırma dosyası. | ✅ **Aktif.** |

---

## 📁 2. `backend/api/app` – Dış Dünya Bağlantısı (FastAPI)

Burası, asistanın kullanıcıyla (Web/Mobil) konuştuğu kapıdır.

### 📍 Ana Dosyalar

* **`main.py`**: Uygulamanın giriş noktası. CORS ayarlarını yapar ve rotaları (HTTP/WebSocket) ayağa kaldırır. ✅ **Çalışıyor.**
* **`connection_manager.py`**: WebSocket bağlantılarını yönetir. Kim bağlandı, kim çıktı takibini yapar. ✅ **Çalışıyor.**
* **`shared_state.py`**: Oturumlar ve bağlantılar arasındaki ortak verileri (hafızayı) tutar. ✅ **Çalışıyor.**

### 📂 Alt Klasörler ve Detaylar

#### **1. `auth/` (Yetkilendirme)**

* **`firebase_auth.py`**: Kullanıcı girişlerini doğrulamak için planlanmış.
* ⚠️ **DURUM:** **Çalışmıyor (Stub).** İçinde `NotImplementedError` var. Firebase entegrasyonu henüz kodlanmamış, sadece yeri ayrılmış.

#### **2. `repositories/` (Veri Deposu)**

* **`session_repo.py`**: Sohbet geçmişini **RAM üzerinde** tutar.
* ✅ **DURUM:** **Çalışıyor.** Ancak uygulama kapanınca veriler silinir. (İleride Redis veya PostgreSQL gerekecek).

#### **3. `services/` (İş Mantığı)**

* **`agent_service.py`**: API ile Agent arasındaki köprüdür. Yapay zekadan gelen yanıtları "stream" (akış) şeklinde WebSocket'e iletir. ✅ **Çalışıyor.**
* **`session_service.py`**: Sohbet geçmişini temizler veya agent için hazır hale getirir. ✅ **Çalışıyor.**

#### **4. `routes/` (Yollar)**

* **`ws_routes.py`**: Gerçek zamanlı mesajlaşma (WebSocket) hattı. `USE_REAL_AGENT` açıksa yapay zekayı konuşturur. ✅ **Çalışıyor.**
* **`http_routes.py`**: Dosya yükleme, sağlık kontrolü (health check) ve test mesajları için kullanılan standart HTTP uçları. ✅ **Çalışıyor.**

#### **5. `store/` (Sahte Veri)**

* **`mock_store.py`**: Gerçek bir veritabanı bağlamadan önce sistemi test etmek için kullanılan "sahte" mesaj ve dosya deposu. ✅ **Çalışıyor.**

---

### 📝 Genel Özet ve Kritik Notlar

1. **Sistem Mimarin Sağlam:** Dosyaların birbirinden bağımsız olması (Decoupled) çok iyi. Agent'ı bozmadan API'yi, API'yi bozmadan Agent'ı güncelleyebilirsin.
2. **Eksik Parça (Auth):** Firebase tarafı şu an tamamen boş. Kullanıcı girişi eklemek istersen ilk durağın `firebase_auth.py` olmalı.
3. **Kalıcılık (Persistence):** Şu an her şey RAM üzerinde. Sunucuya reset atarsan tüm mesaj geçmişi uçar.
4. **Araçlar (Tools):** Web araması için `TAVILY_API_KEY` değişkenini `.env` dosyasına eklemeyi unutma.

**Bir sonraki adımda ne yapmak istersin?**

* "Firebase giriş sistemini aktif edelim."
* "Verileri RAM yerine gerçek bir veritabanına kaydedelim."
* "Agent'a yeni bir yetenek (tool) ekleyelim."

Hangi tarafa odaklanalım?