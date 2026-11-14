from .._base import Category, Tool
from .tools import MemoryInput, hafizada_ara

CATEGORY = Category(
    id="memory", name="Hafıza", description="Kullanıcının hafızaya kaydettiği geçmiş konuşmalar.",
    tools=(Tool(id="hafizada_ara", name="Hafızada ara",
                description=("Kullanıcının hafızaya kaydettiği geçmiş konuşmalarda arar. Kullanıcı kendisiyle ilgili kişisel bir şeyi "
                             "(adı, tercihleri, daha önce konuştukları, anlattıkları) sorarsa ve bunu bilmiyorsan tahmin etme, "
                             "önce bu aracı çağır. 'sorgu' aranacak şeyin kısa tarifidir."),
                input_model=MemoryInput, handler=hafizada_ara, timeout_seconds=45),),
)
