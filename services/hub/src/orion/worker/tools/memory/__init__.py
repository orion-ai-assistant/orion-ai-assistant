from .._base import Category, Tool
from .tools import (MemoryContextInput, MemoryDeleteInput, MemoryOpenInput, MemorySearchInput, RememberInput,
                    delete_memory, memory_context, open_memory_files, remember, search_memory)

CATEGORY = Category(
    id="memory", name="Hafıza", description="Kullanıcının hafızaya kaydettiği notlar, bilgiler, olaylar ve dosyalar (yalnız Asistanla konuş'ta).",
    tools=(Tool(id="search_memory", name="Hafızada ara",
                description=(
                    "Searches the user's memory (notes, facts, events, files, images, audio, video). Use it ONLY when the "
                    "user asks about the past or about something personal (their people, plans, preferences), never on your own "
                    "for an ordinary question; ask it before guessing anything personal. Each query is a text (by meaning), a keyword (exact words only) or "
                    "an id from an earlier result (memories like that one); like=false leaves matching memories out. "
                    "mode together = close to all liked queries, separate = close to any. Results are pages, best "
                    "first; with no query, the newest (of kind). The order is the shown score, nothing else. With the "
                    "user's memory collections on: 'about' limits it to one person or thing, 'when' is the one date filter "
                    "(it also lists replaced fact versions true then as 'past_facts'), 'include_past' lists how facts used to be (by words), 'budget' fits the answer into a "
                    "token budget, and a query that names a known person or thing also returns that 'entity' card "
                    "(its current facts and latest events) before the results. A fact carries its 'since'; one that "
                    "changed has 'history' {versions, last_change} (no old text: ask memory_context for the earlier versions). An "
                    "event's 'said_as' carries the day it was said ('geçen hafta, said on 2026-11-18')."),
                input_model=MemorySearchInput, handler=search_memory, timeout_seconds=45, default_on=True),
           Tool(id="remember", name="Hatırla",
                description=("When the user asks you to remember something, in ANY language ('remember this', 'note that', 'don't forget' or the same in theirs), "
                             "or tells you something personal and asks you to keep it, call this with their words in 'text', "
                             "in their own language, never translated. You do not choose what kind of memory it is: a "
                             "background writer files it (a fact, an event, a person, a changed relation or a note) and "
                             "anything that changes what is already known waits for the user's card. It only queues: answer "
                             "that it is noted. Never use it without being asked. Example: {text: 'Sevgilimden ayrıldım, "
                             "yeni sevgilim Merve.'}."),
                input_model=RememberInput, handler=remember, timeout_seconds=30, default_on=True),
           Tool(id="delete_memory", name="Hafızadan sil",
                description=("ONLY when the user asks to forget or delete something ('forget this', 'delete it from your memory'). Find the memories with "
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
