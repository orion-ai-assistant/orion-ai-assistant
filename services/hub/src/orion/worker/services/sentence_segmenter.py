"""Bounded, incremental speech text; punctuation may straddle token boundaries."""
import re


class SentenceSegmenter:
    # Only a sentence longer than ~25 s of speech (11.5 characters a second) is cut at a word: the speech service
    # stops at ~30 s of audio. AdaptiveTts.MAX_BATCH_CHARS is the same bound.
    MAX_CHARS = 287
    MAX_PENDING = 12_000
    ABBREVIATIONS = {"dr", "prof", "doç", "sn", "say", "örn", "vb", "vs", "bkz", "mr", "mrs", "ms", "no"}

    def __init__(self):
        self.pending = ""
        self.in_code = False

    def feed(self, text, final=False):
        self.pending += text
        if len(self.pending) > self.MAX_PENDING:
            raise ValueError("Speech text buffer exceeds limit")
        result = []
        while self.pending:
            if self.in_code:
                end = self.pending.find("```")
                if end < 0:
                    # Retain only a possible partial closing fence.
                    self.pending = self.pending[-2:] if not final else ""
                    break
                self.pending = self.pending[end + 3:]
                self.in_code = False
                continue
            fence = self.pending.find("```")
            if fence == 0:
                self.pending = self.pending[3:]
                self.in_code = True
                continue
            limit = fence if fence >= 0 else len(self.pending)
            inline = self.pending.find("`")
            if 0 <= inline < limit:
                closing = self.pending.find("`", inline + 1)
                if closing < 0:
                    if inline == 0 and final:
                        self.pending = ""
                        break
                    limit = inline
                    if not limit:
                        break
                else:
                    self.pending = self.pending[:inline] + " " * (closing + 1 - inline) + self.pending[closing + 1:]
                    continue
            boundary = None
            # A line break ends a line the model wrote on its own (a list item, "Tavşan: ...", a lead-in);
            # sentences are looked for only up to it.
            newline = self.pending.find("\n", 0, limit)
            line_end = newline if newline >= 0 else limit
            for match in re.finditer(r'[.!?…]+["”’)\]*_~]*', self.pending[:line_end]):
                end = match.end()
                if end == len(self.pending) and not final:
                    continue  # Need lookahead for decimals/URLs/abbreviations.
                if end < len(self.pending) and not self.pending[end].isspace():
                    continue
                before = self.pending[:match.start()]
                word = re.search(r'(\S+)$', before)
                word = word.group(1).strip("*_~") if word else ""
                punctuation = match.group(0)
                if punctuation == "." and (word.lower() in self.ABBREVIATIONS or
                    re.fullmatch(r'[A-ZÇĞİÖŞÜ]', word) or re.fullmatch(r'(?:[A-Za-zÇĞİÖŞÜçğıöşü]\.)+[A-Za-zÇĞİÖŞÜçğıöşü]', word)):
                    continue
                # “Muuuu!” diye bağırmış: a closing quote after the punctuation followed by a lowercase word is a
                # dialogue tag, not the end of the sentence. Only a closing quote waits for the next word, so a plain
                # "Bir bakayım!" before a tool call is still spoken at once.
                if any(mark in punctuation for mark in '"”’)]') and end != newline:
                    following = self.pending[end:].lstrip()
                    if not following and not final:
                        continue  # the next word has not arrived yet
                    if following[:1].islower():
                        continue
                # Satıcı, “Bu yumurta çok akıllı! İçinden ... veriyor” demiş: never cut inside an open quotation.
                if self.pending[:end].count("“") > self.pending[:end].count("”"):
                    continue
                boundary = end
                break
            if boundary is None and newline >= 0:
                following = self.pending[newline + 1:].lstrip(" \t")
                if not following and not final:
                    break  # is the next line a new one, or the same sentence wrapped?
                if following[:1].islower():
                    # Only wrapped: the sentence goes on in the next line.
                    self.pending = self.pending[:newline] + " " + self.pending[newline + 1:]
                    continue
                boundary = newline + 1
            if boundary is None and limit >= self.MAX_CHARS:
                boundary = self.pending.rfind(" ", 0, self.MAX_CHARS + 1)
                if boundary < self.MAX_CHARS // 2:
                    boundary = self.MAX_CHARS
            if boundary is None and fence > 0:
                boundary = fence
            if boundary is None:
                if not final:
                    break
                boundary = limit
            # Even a completed very long sentence is split at word boundaries.
            if boundary > self.MAX_CHARS:
                boundary = self.pending.rfind(" ", 0, self.MAX_CHARS + 1)
                if boundary < self.MAX_CHARS // 2:
                    boundary = self.MAX_CHARS
            piece, self.pending = self.pending[:boundary], self.pending[boundary:]
            piece = self.clean(piece)
            if piece:
                result.append(piece)
        return result

    @staticmethod
    def clean(text):
        text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'`[^`]*`', '', text)
        text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*>]\s+)', '', text)
        text = re.sub(r'[*_~]', '', text)
        text = EMOJI.sub('', text)
        return re.sub(r'\s+', ' ', text).strip()


# Emoji are not read aloud ("😄" became a second of noise). Pictographs, dingbats, flags, skin tones and the joiners
# and variation selectors that glue them; "1️⃣" keeps its digit. Letters, digits, ° and other text marks stay.
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF⌀-⏿☀-➿⬀-⯿‍⃣︎️\U000E0020-\U000E007F]"
)
