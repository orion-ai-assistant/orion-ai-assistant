# Hub'a fonksiyon ekleme

Yeni kategoriyi `src/orion/worker/tools/<kategori>/` paketi olarak ekleyin. `__init__.py` dosyası `CATEGORY` sunar; küçük kategorilerde fonksiyonlar ve girdi modelleri `tools.py` içinde birlikte durur. Büyüyen kategorilerde `search.py`, `transcript.py` gibi görev dosyaları kullanın. `datetime/` ve `text/` çalışan örneklerdir. Registry hem paketleri hem eski tek dosyalı kategorileri keşfeder; alt dosyaları kategori olarak taramaz. Ortak altyapı dosyalarının adı `_` ile başlamalıdır.

Küçük bir kategorinin `tools.py` dosyası:

```python
from pydantic import Field
from orion.worker.tools._base import Tool, ToolContext, ToolInput

class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200, description="Search terms")

async def search(args: SearchInput, context: ToolContext) -> dict:
    if await context.cancelled():
        return {"error": "cancelled"}
    # Burada kendi async servisinizi çağırın; sonuç JSON'a dönüştürülebilir olmalı.
    return {"matches": []}

SEARCH = Tool(
        id="search_sources", name="Kaynaklarda ara",
        description="Search available sources using query terms.",
        input_model=SearchInput, handler=search, timeout_seconds=20,
)
```

Aynı paketin `__init__.py` dosyası araçları açıkça listeler:

```python
from .._base import Category
from .tools import SEARCH

CATEGORY = Category(
    id="search", name="Arama", description="İzin verilen kaynaklarda arama.",
    tools=(SEARCH,),
)
```

Kategoriye birden çok fonksiyon eklemek için `tools` tuple'ına başka `Tool` tanımları koyun. Kategori ve fonksiyon kimlikleri kalıcı kayıtlarda kullanılır; adları sonradan değiştirmeyin. Pydantic giriş modelinden Router'a gidecek JSON Schema üretilir; `ToolInput` ek alanları reddeder. Açıklamalar modelin fonksiyonu ne zaman çağıracağını belirlemesine yardımcı olur.

`ToolContext` kullanıcı, sohbet ve tur kimliklerini ve `cancelled()` denetimini verir. Uzun işlemlerde bu denetimi tekrarlayın. Ağ istekleri ve yan etkiler modül import edilirken değil handler çalışırken yapılmalıdır. Handler async olmalı, sonucu JSON'a dönüştürülebilmeli, 30 saniyede bitmeli ve 32 KiB'yi geçmemelidir. Dosya silme gibi geri alınamayacak işlemlere ayrı, açık bir yetkilendirme tasarlayın; bu örnek kategori onların çalıştırılması için izin vermez.

Dosyayı ekledikten sonra API ve worker süreçlerini yeniden başlatın. Yeni fonksiyon katalogda görünür, ancak varsayılan kapalıdır. Kullanıcı Ayarları'ndaki **Varsayılan fonksiyonlar** veya sohbetin **+ → Fonksiyonlar** menüsünden açabilirsiniz. Var olan sohbet özel seçimleri yeni fonksiyonu kendiliğinden açmaz.

Doğrulama için `test_tools.py` testlerini çalıştırın; yeni fonksiyon için geçerli/geçersiz parametre ve iptal testleri ekleyin. Dashboard'da açık fonksiyonla çağrı kartı, sonuç ve nihai yanıtı; kapalı fonksiyonla Router'a şemanın gönderilmediğini kontrol edin.

## Hazır Python kütüphaneleri ve LangChain araçları

`_adapters.py` iki giriş sağlar: SDK fonksiyonları için `from_callable`,
`invoke` / `ainvoke` arayüzlü LangChain araçları için `from_langchain`.
Hub'ın LangChain veya LangGraph ile yeniden yazılması gerekmez. Adaptörlerin
kendisi bu paketleri import etmez; sadece kullanacağınız sağlayıcının paketini
kurun ve bağımlılığını `pyproject.toml` içinde bildirin. Yerleşik web araması
DDGS kullanır; diğer sağlayıcılar için uygun bir optional dependency grubu eklenebilir.

Orion kimliği, açıklaması ve `ToolInput` modeli bize aittir. Sağlayıcının şemasını
otomatik kopyalamak yerine modele sunulan parametreleri açıkça seçin. Böylece
sağlayıcı değişse bile kayıtlı tool kimliği ve kullanıcı seçimleri korunur.

Async SDK entegrasyon taslağı (`your_sdk` gerçek sağlayıcıyla değiştirilmelidir):

```python
from pydantic import Field
from .._base import ToolInput
from .._adapters import from_callable

class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)

def make_search(context):
    from your_sdk import AsyncClient  # Yalnızca çağrı sırasında import edilir.

    async def search(query):
        async with AsyncClient(timeout=10) as client:
            return await client.search(query=query)

    return search

SEARCH = from_callable(
    id="search_sources", name="Kaynaklarda ara", description="Search sources.",
    input_model=SearchInput, factory=make_search,
    map_output=lambda result: {"items": result["items"][:5]},
    timeout_seconds=15,
)
```

`factory(context)` her çağrıda çalışır. Kullanıcıya ait bağlantıyı
`context.user_id` üzerinden seçebilirsiniz. API anahtarları model girdisi olmaz.
Factory async modda hızlı ve ağ isteği yapmayan bir fonksiyon olmalıdır; istemci
yaşam döngüsü ve kapatma işlemi yukarıdaki gibi çağrının içinde yönetilmelidir.
`map_input(args)` SDK'ya verilecek keyword argümanları, `map_output(result)` ise
JSON'a çevrilebilir, küçük bir sonucu üretir. Varsayılan girdi `args.model_dump()`,
varsayılan çıktı sağlayıcının sonucudur.

LangChain için aynı kayıt şu biçimdedir:

```python
from .._adapters import from_langchain

def make_external_tool(context):
    from your_provider import SearchTool  # Entegrasyona özel paket/sınıf.
    return SearchTool()  # Sağlayıcının kendi istek timeout'unu da yapılandırın.

SEARCH = from_langchain(
    id="search_sources", name="Kaynaklarda ara", description="Search sources.",
    input_model=SearchInput, factory=make_external_tool,
    native_async=False, read_only=True,
    map_input=lambda args: {"query": args.query},
    timeout_seconds=15,
)
```

Bu örnek yalnızca entegrasyon taslağıdır; gerçek sağlayıcı seçilip test edilmelidir.
`native_async=True` sağlayıcının gerçekten iptal edilebilir async uygulaması
olduğunu bildiğinizde kullanılır. Sadece `ainvoke` metodunun bulunması yeterli
değildir: LangChain bazı araçlarda thread'e geri döner.
`native_async=False` seçimi `invoke` metodunu ayrı thread'de çalıştırır ve açıkça
`read_only=True` gerektirir. SDK fonksiyonları için karşılığı
`execution="thread", read_only=True` olur. Bu bildirim bir izin sistemi değildir;
entegrasyon yazarının kontrol etmesi gereken bir sözleşmedir.

Thread çalışması iptal/timeout sonrasında arka planda devam edebilir. Bu nedenle
sağlayıcının kendi ağ timeout'u zorunludur; yazma, silme ve Blender sahnesini
değiştirme gibi işlemler için bu yolu kullanmayın. Kooperatif async bağlantı veya
yaşam döngüsü yönetilen ayrı uygulama/iş süreci tasarlayın. Blender gibi araçlar
uygun uygulama ortamı, eklenti veya RPC bağlantısı gerektirebilir; adaptör bunları
kendiliğinden kurmaz. Adaptörler otomatik yeniden deneme yapmaz.

LangChain girdisi sıradan string/dict olabilir; `ToolCall` zarfı göndermeyin.
`Document`, `ToolMessage` veya sağlayıcıya özel nesneleri `map_output` ile
başlık/URL/özet gibi alanlara dönüştürün. Async kaynak hataları, doğrulama,
30 saniye üst sınırı ve 32 KiB sonuç sınırı mevcut Orion çalıştırıcısından geçer.
Eksik paket çağrı hatası oluşturur; import factory içinde kaldığı sürece katalog
ve diğer kategoriler çalışmaya devam eder.

İlgili testler (PowerShell, depo kökünden; pytest gerektirmez):

```powershell
$env:PYTHONPATH = "services/hub/src;."
services/hub/.venv/Scripts/python.exe -m unittest discover -s services/hub/tests -p "test_tool*.py" -v
```

## Web araması

`web_search/` kategorisindeki `search_web`, DDGS kütüphanesiyle yalnızca
DuckDuckGo kaynağında arama yapar; API anahtarı gerektirmez. `query` ve isteğe
bağlı `max_results` (1-5, varsayılan 5) alır; başlık, URL ve kısa özet döndürür.
Tam sayfa içeriği getirmez. Sağlayıcı hataları boş sonuç gibi gizlenmez.
Kaynak metinleri güvenilmeyen veri olarak değerlendirilmelidir.

Hub bağımlılıklarını güncelledikten sonra API ve worker'ı yeniden başlatın.
Araç varsayılan kapalıdır; sohbetin **+ → Fonksiyonlar → Web araması** menüsünde
kategoriyi ve **Web'de ara** fonksiyonunu açın. Sağlayıcı istek timeout'u 8 saniye,
Orion çağrı timeout'u 20 saniyedir. Senkron sağlayıcı read-only thread adaptörünü
kullanır; iptal edilen ağ işi kendi timeout'una kadar arka planda devam edebilir.
Sonuç sayısı ve metin uzunlukları Orion'un çıktı sınırına göre kısıtlanır.

`tools/` dizini boş `__init__.py` olmadan namespace package olarak çalışır.
Kategori klasörlerindeki `__init__.py` dosyaları ise `CATEGORY` kaydını taşır;
bunları silmeyin. Paket keşfi ve web araması `test_web_search.py` ile doğrulanır.
Kök `orion/__init__.py` korunmalıdır: bu ortamda komşu Router projesinin
`orion.py` modülüyle isim çakışmasını engeller.
