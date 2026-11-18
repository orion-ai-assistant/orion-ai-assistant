"""The extraction pre-filter (docs/hafiza-tasarimi.md §7.2 step 2): a cheap, LLM-free look at a chat's NEW USER messages that
decides whether the chat is worth sending to the background model at all. Pure functions: no database, no network.

It looks only at what the user wrote and asks one question: does any of it talk about the user or their surroundings? Three
kinds of signal, each a plain word list (folded with memory.fold: Turkish letters without their marks, lower case):

- first person / belonging: "ben, benim, bana, I, my, me ...", family and close people ("annem, kızım, my wife"), things that
  are "mine" ("evim, işim"), and first-person verb endings of Turkish ("gittim", "severim", "yapıyorum", "taşınacağım");
- a name the memory already knows (an entity's name, relation or alias, memory_entities `keys`);
- a date expression: a relative day word ("bugün", "yesterday"), a weekday, a month name, an ISO date, "12.05.2026",
  "3 Mart".

None of them: the chat is dropped and the reason is written to the run (`prefilter = dropped:<reason>`), so a filter that drops
too much shows in the quality-gate report. The lists are deliberately generous (a false pass costs one cheap model call, a false
drop loses a memory) and are plain module constants, so adding a language is adding words; they are not tuned on any data.
"""
from __future__ import annotations

import re

from orion.kernel import memory

MIN_KEY = 3  # A known name shorter than this (after folding) is not matched: it would hit inside every other word.

FIRST_PERSON = frozenset(memory.fold(w) for w in (
    "ben", "benim", "bana", "beni", "bende", "benden", "bence", "bizim", "bize", "biz", "bizi", "bizde", "kendim", "kendime",
    "i", "i'm", "im", "i've", "ive", "i'll", "i'd", "my", "me", "mine", "myself", "we", "our", "us"))

BELONGING = frozenset(memory.fold(w) for w in (
    # Turkish: people and things that are "mine" (possessive forms).
    "annem", "babam", "kardeşim", "eşim", "kızım", "oğlum", "çocuğum", "çocuklarım", "arkadaşım", "arkadaşlarım", "dedem",
    "ninem", "babaannem", "anneannem", "amcam", "dayım", "teyzem", "halam", "kuzenim", "torunum", "damadım", "gelinim",
    "kayınvalidem", "kayınpederim", "sevgilim", "nişanlım", "karım", "kocam", "patronum", "öğretmenim", "komşum", "kedim",
    "köpeğim", "evim", "işim", "işyerim", "okulum", "arabam", "adresim", "doğum günüm", "yaşım",
    # English: people who are the user's without "my" in front.
    "mom", "mum", "dad", "mother", "father", "wife", "husband", "daughter", "son", "sister", "brother", "girlfriend",
    "boyfriend", "fiancee", "fiance", "grandma", "grandpa", "grandmother", "grandfather", "aunt", "uncle", "cousin"))

# First-person verb endings of Turkish on folded words (ı/i/u/ü and ö/o are one letter after folding):
# past (-dım, -tım), present (-yorum), aorist (-arım, -erim, -irim, -urum), future (-ecegim, -acagim).
FIRST_PERSON_ENDING = re.compile(r"^\w{3,}(?:dim|dum|tim|tum|orum|arim|erim|irim|urum|ecegim|acagim)$")

DATE_WORDS = frozenset(memory.fold(w) for w in (
    "bugün", "dün", "yarın", "evvelsi", "önceki", "geçen", "gelecek", "haftaya", "dünkü", "sabah", "akşam",
    "today", "yesterday", "tomorrow", "tonight", "last", "next",
    "pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"))
DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b"),             # 2026-11-17, 2026-11
    re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b"),      # 17.11.2026, 17/11/26
)
WORD = re.compile(r"[\w']+")


def _words(text: str) -> list[str]:
    return WORD.findall(memory.fold(text))


def signals(user_texts: list[str], known_keys: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Which kinds of signal the user's messages carry: any of "first_person", "belonging", "ending", "known_name", "date"
    (empty: none). `known_keys`: folded names, relations and aliases of the user's entities."""
    found: list[str] = []
    keys = {k for k in known_keys if len(k) >= MIN_KEY}
    multiword = [k for k in keys if " " in k]
    for text in user_texts:
        folded = memory.fold(text)
        words = _words(text)
        word_set = set(words)
        if word_set & FIRST_PERSON and "first_person" not in found:
            found.append("first_person")
        if word_set & BELONGING and "belonging" not in found:
            found.append("belonging")
        if "ending" not in found and any(FIRST_PERSON_ENDING.match(w) for w in word_set):
            found.append("ending")
        if "known_name" not in found and (word_set & keys or any(k in folded for k in multiword)):
            found.append("known_name")
        if "date" not in found and (word_set & DATE_WORDS or any(p.search(folded) for p in DATE_PATTERNS)):
            found.append("date")
    return found


def prefilter(user_texts: list[str], known_keys: set[str] | frozenset[str] = frozenset()) -> str:
    """"passed:<signals>" or "dropped:<reason>" (no_user_text | no_personal_signal) for the new user messages."""
    texts = [t for t in user_texts if (t or "").strip()]
    if not texts:
        return "dropped:no_user_text"
    found = signals(texts, known_keys)
    return "passed:" + "+".join(found) if found else "dropped:no_personal_signal"
