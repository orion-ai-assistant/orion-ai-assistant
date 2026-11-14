from .._base import Category, Tool
from .tools import MemoryAddInput, MemoryDeleteInput, MemoryInput, hafizada_ara, hafizadan_sil, hafizaya_ekle

CATEGORY = Category(
    id="memory", name="Hafıza", description="Kullanıcının hafızaya kaydettiği geçmiş konuşmalar ve notlar.",
    tools=(Tool(id="hafizada_ara", name="Hafızada ara",
                description=("Kullanıcının hafızaya kaydettiği geçmiş konuşmalarda arar. Kullanıcı kendisiyle ilgili kişisel bir şeyi "
                             "(adı, tercihleri, daha önce konuştukları, anlattıkları) sorarsa ve bunu bilmiyorsan tahmin etme, "
                             "önce bu aracı çağır. 'sorgu' aranacak şeyin kısa tarifidir."),
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
                input_model=MemoryDeleteInput, handler=hafizadan_sil, timeout_seconds=30),),
)
