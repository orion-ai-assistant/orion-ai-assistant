from .._base import Category, Tool
from .tools import (MemoryAddInput, MemoryContextInput, MemoryDeleteInput, MemoryOpenInput, MemorySearchInput,
                    add_memory, delete_memory, memory_context, open_memory_files, search_memory)

CATEGORY = Category(
    id="memory", name="Hafıza", description="Kullanıcının hafızaya kaydettiği geçmiş konuşmalar ve notlar.",
    tools=(Tool(id="search_memory", name="Hafızada ara",
                description=(
                    "Searches what the user saved to memory: chats, notes, text files, images, audio, video. If the "
                    "user asks about something personal (their name, preferences, what you talked about, what they "
                    "told or showed you) and you do not know it, do not guess: search first. Each query is a short "
                    "text or the id of a memory part from an earlier result (to find memories like that image, "
                    "recording or text), marked like=true (results should be like it) or like=false (leave out "
                    "memories like it), e.g. 'like the cat photo (id 41) but not a dog'. Several liked queries: "
                    "mode together (close to all of them) or separate (close to any one; each result names which). "
                    "kind limits to text, image, audio or video; with no queries the newest memories of that kind "
                    "come (e.g. 'what images are in my memory'). Results are pages, best first; each says its score, "
                    "which query gave it and how far above the search's average it stands. If what you need is not "
                    "on page 1 and 'more' is true, ask for page 2, 3..."),
                input_model=MemorySearchInput, handler=search_memory, timeout_seconds=45, default_on=True),
           Tool(id="add_memory", name="Hafızaya ekle",
                description=("Saves something to remember when the user says 'remember this', 'keep in mind', "
                             "'don't forget'. 'text' is one short self-contained sentence (e.g. 'The user's "
                             "birthday is 3 March.'). Never save without being asked."),
                input_model=MemoryAddInput, handler=add_memory, timeout_seconds=45, default_on=True),
           Tool(id="delete_memory", name="Hafızadan sil",
                description=("When the user says 'forget this', 'delete it from your memory'. Find the memories with "
                             "search_memory first and pass only the ids of what the user wants forgotten; "
                             "whole_memory=true deletes everything saved together with each id. If unsure, ask "
                             "before deleting. Then tell the user what you deleted."),
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
