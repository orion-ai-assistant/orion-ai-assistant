from .._base import Category, Tool
from .tools import (MemoryAddInput, MemoryDeleteInput, MemoryInput, MemoryOpenInput, hafizada_ara, hafizadaki_medyayi_ac,
                    hafizadan_sil, hafizaya_ekle)

CATEGORY = Category(
    id="memory", name="Hafıza", description="Kullanıcının hafızaya kaydettiği geçmiş konuşmalar ve notlar.",
    tools=(Tool(id="hafizada_ara", name="Hafızada ara",
                description=("Kullanıcının hafızaya kaydettiği geçmiş konuşmalarda arar. Kullanıcı kendisiyle ilgili kişisel bir şeyi "
                             "(adı, tercihleri, daha önce konuştukları, anlattıkları) sorarsa ve bunu bilmiyorsan tahmin etme, "
                             "önce bu aracı çağır. 'sorgu' aranacak şeyin kısa tarifidir. Sonuçlar en iyiden başlayarak 10'arlı "
                             "sayfalardır; aradığın ilk sayfada yoksa ve 'daha_var' true ise 'sayfa' 2, 3... ile devam et. "
                             "'tur' ile yalnız görsel, ses ya da metin kayıtlarına bakılır; 'sorgu' boşsa o türün en yeni "
                             "kayıtları gelir (ör. 'hafızadaki görsellerde neler var')."),
                input_model=MemoryInput, handler=hafizada_ara, timeout_seconds=45),
           Tool(id="hafizaya_ekle", name="Hafızaya ekle",
                description=("Kullanıcı 'bunu hatırla', 'aklında tut', 'unutma' gibi bir şey söylediğinde hatırlanacak bilgiyi "
                             "hafızaya kaydeder. 'metin' kendi başına anlaşılır kısa bir cümle olsun (ör. 'Kullanıcının doğum "
                             "günü 3 Mart.'). Kullanıcı istemeden kaydetme."),
                input_model=MemoryAddInput, handler=hafizaya_ekle, timeout_seconds=45),
           Tool(id="hafizadan_sil", name="Hafızadan sil",
                description=("Kullanıcı 'bunu unut', 'hafızandan sil' dediğinde kullanılır. Önce hafizada_ara ile ilgili kayıtları "
                             "bul, yalnızca kullanıcının unutmanı istediği şeyi içeren kayıtların 'id' değerlerini 'idler' olarak "
                             "ver. Emin değilsen silmeden önce kullanıcıya sor. Sonra neyi sildiğini kullanıcıya söyle."),
                input_model=MemoryDeleteInput, handler=hafizadan_sil, timeout_seconds=30),
           Tool(id="hafizadaki_medyayi_ac", name="Hafızadaki medyayı aç",
                description=("hafizada_ara'nın bulduğu görsel veya ses kayıtlarının içeriğine bakmak için kullanılır "
                             "(ör. 'o fotoğrafta ne vardı', 'kaydettiğim sesi dinle'). 'idler' hafizada_ara sonucundaki "
                             "'id' değerleri, en fazla 3. Dosyalar araç sonucundan hemen sonra gelen mesajda görünür ve "
                             "sohbette kalır; aynı dosyayı tekrar açma."),
                input_model=MemoryOpenInput, handler=hafizadaki_medyayi_ac, timeout_seconds=30),),
)
