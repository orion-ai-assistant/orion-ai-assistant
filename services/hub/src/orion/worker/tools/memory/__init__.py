from .._base import Category, Tool
from .tools import (MemoryAddInput, MemoryContextInput, MemoryDeleteInput, MemoryOpenInput, MemorySearchInput,
                    add_memory, delete_memory, memory_context, open_memory_files, search_memory)

CATEGORY = Category(
    id="memory", name="Hafıza", description="Kullanıcının hafızaya kaydettiği geçmiş konuşmalar ve notlar.",
    tools=(Tool(id="search_memory", name="Hafızada ara",
                description=(
                    "Searches the user's memory (chats, notes, files, images, audio, video). Ask it before "
                    "guessing anything personal. Each query is a text (by meaning), a keyword (exact words only) or "
                    "an id from an earlier result (memories like that one); like=false leaves matching memories out. "
                    "mode together = close to all liked queries, separate = close to any. Results are pages, best "
                    "first; with no query, the newest (of kind). The order is the shown score, nothing else. With the "
                    "user's memory collections on: 'about' limits it to one person or thing, 'as_of' / 'include_past' "
                    "bring in how facts stood or used to be ('past_facts', by words), 'budget' fits the answer into a "
                    "token budget, and a query that names a known person or thing also returns that 'entity' card "
                    "(its current facts and latest events) before the results."),
                input_model=MemorySearchInput, handler=search_memory, timeout_seconds=45, default_on=True),
           Tool(id="add_memory", name="Hafızaya ekle",
                description=("Saves something to remember when the user says 'remember this', 'keep in mind', "
                             "'don't forget' (or tells you what happened). Never save without being asked. ALWAYS write "
                             "everything (text, about, relation, place, participants) in the user's own language, exactly "
                             "as they said it: never translate and never add English labels. Fields by type. "
                             "type=note (default): 'text' (+ optional 'title'). "
                             "type=fact (when the user's memory collections are on): 'text' = ONE short sentence in the "
                             "user's words ('Annemin adı Ayşe.'), 'about' = who it is about as the user says it ('Annem', "
                             "'Mehmet'; empty or 'me' for the user: their own name, age, job), 'relation' = how a new person "
                             "relates to the user ('annem'), 'category', optional 'since'. Example: {type:'fact', "
                             "text:'Annemin adı Ayşe.', about:'Annem', relation:'annem', category:'relationship'}. A changed "
                             "fact is passed with 'replaces' (a card asks the user, you never close or rewrite a fact); a "
                             "locked kind (health, religion, money ...) also only opens a card. "
                             "type=event: 'text' = what happened ('Mehmet ile parkta kahve içtik.'), 'said_as' = the user's "
                             "own time words ('geçen gün'), 'date' only if the user gave one (YYYY-MM-DD), 'participants' "
                             "(names, 'me' for the user), 'place'. If the user gave no exact day, KEEP said_as, set "
                             "precision='approx' and do not invent a date. Example: {type:'event', text:'Mehmet ile parkta "
                             "kahve içtik.', said_as:'geçen gün', precision:'approx', participants:['Mehmet'], place:'park'}. "
                             "Leave unused fields out. The answer may say same_as: it was already known."),
                input_model=MemoryAddInput, handler=add_memory, timeout_seconds=45, default_on=True),
           Tool(id="delete_memory", name="Hafızadan sil",
                description=("When the user says 'forget this', 'delete it from your memory'. Find the memories with "
                             "search_memory first and pass only the ids of what the user wants forgotten; "
                             "whole_memory=true takes everything saved together with each id. If unsure, ask "
                             "first. This only ASKS: the user sees a confirmation card and its Sil button moves the "
                             "memory to the trash (restorable for a while), so say that it waits for their press "
                             "(never that it is deleted). To learn the outcome call it again with the confirmation_id."),
                input_model=MemoryDeleteInput, handler=delete_memory, timeout_seconds=30, default_on=True),
           Tool(id="open_memory_files", name="Hafızadaki dosyayı aç",
                description=(
                    "Opens stored memory files by their search_memory ids (up to 3): images, audio, video, and text "
                    "documents (.txt/.md files, notes). Each file is put in front of the user under your answer as "
                    "an attachment they can click to view, play or read; you see images and hear audio (a video "
                    "part as a few frames and its sound) when the chat model can, and get a document's text. Use it "
                    "when the user wants to see, hear, read or get a memory file ('show it', 'open it in the chat', "
                    "'what was in that photo'). Difference from memory_context: memory_context only gives YOU text "
                    "(neighbouring parts, the whole memory, the chat it was saved from); open_memory_files shows "
                    "the original file to the USER. A file already opened in this chat is not attached again."),
                input_model=MemoryOpenInput, handler=open_memory_files, timeout_seconds=30, default_on=True),
           Tool(id="memory_context", name="Hafıza kaydının bağlamı",
                description=(
                    "Gives you, as text, more of a memory search_memory found: the parts saved with it (the talk "
                    "next to an image, every part of a long file) in full, or with neighbours=1-3 only the parts "
                    "around the matching one. chat=true also brings the messages of the chat it was saved from "
                    "(around the saved message, or the chat's start): use it for 'what did we say in that "
                    "conversation'. It says when that chat was deleted or cleared. It shows the user nothing: to "
                    "put a file in front of the user, use open_memory_files."),
                input_model=MemoryContextInput, handler=memory_context, timeout_seconds=30, default_on=True),),
)
