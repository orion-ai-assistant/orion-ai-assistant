# Hub'a fonksiyon ekleme

Yeni bir kategori için `src/orion/worker/tools/` altında bir Python dosyası oluşturun. Dosya adının başına `_` koymayın. `datetime_tools.py` çalışan örnektir. Dosya `CATEGORY` adlı bir `Category` nesnesi sunmalıdır; API ve worker açılışta klasörü tarayarak kategorileri otomatik bulur.

```python
from pydantic import Field
from orion.worker.tools._base import Category, Tool, ToolContext, ToolInput

class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200, description="Search terms")

async def search(args: SearchInput, context: ToolContext) -> dict:
    if await context.cancelled():
        return {"error": "cancelled"}
    # Burada kendi async servisinizi çağırın; sonuç JSON'a dönüştürülebilir olmalı.
    return {"matches": []}

CATEGORY = Category(
    id="search", name="Arama", description="İzin verilen kaynaklarda arama.",
    tools=(Tool(
        id="search_sources", name="Kaynaklarda ara",
        description="Search available sources using query terms.",
        input_model=SearchInput, handler=search, timeout_seconds=20,
    ),),
)
```

Kategoriye birden çok fonksiyon eklemek için `tools` tuple'ına başka `Tool` tanımları koyun. Kategori ve fonksiyon kimlikleri kalıcı kayıtlarda kullanılır; adları sonradan değiştirmeyin. Pydantic giriş modelinden Router'a gidecek JSON Schema üretilir; `ToolInput` ek alanları reddeder. Açıklamalar modelin fonksiyonu ne zaman çağıracağını belirlemesine yardımcı olur.

`ToolContext` kullanıcı, sohbet ve tur kimliklerini ve `cancelled()` denetimini verir. Uzun işlemlerde bu denetimi tekrarlayın. Ağ istekleri ve yan etkiler modül import edilirken değil handler çalışırken yapılmalıdır. Handler async olmalı, sonucu JSON'a dönüştürülebilmeli, 30 saniyede bitmeli ve 32 KiB'yi geçmemelidir. Dosya silme gibi geri alınamayacak işlemlere ayrı, açık bir yetkilendirme tasarlayın; bu örnek kategori onların çalıştırılması için izin vermez.

Dosyayı ekledikten sonra API ve worker süreçlerini yeniden başlatın. Yeni fonksiyon katalogda görünür, ancak varsayılan kapalıdır. Kullanıcı Ayarları'ndaki **Varsayılan fonksiyonlar** veya sohbetin **+ → Fonksiyonlar** menüsünden açabilirsiniz. Var olan sohbet özel seçimleri yeni fonksiyonu kendiliğinden açmaz.

Doğrulama için `test_tools.py` testlerini çalıştırın; yeni fonksiyon için geçerli/geçersiz parametre ve iptal testleri ekleyin. Dashboard'da açık fonksiyonla çağrı kartı, sonuç ve nihai yanıtı; kapalı fonksiyonla Router'a şemanın gönderilmediğini kontrol edin.
