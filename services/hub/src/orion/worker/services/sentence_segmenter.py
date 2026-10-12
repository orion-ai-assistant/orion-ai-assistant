"""Bounded, incremental speech text; punctuation may straddle token boundaries."""
import re


class SentenceSegmenter:
    MAX_CHARS = 600
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
            for match in re.finditer(r'[.!?…]+["”’)\]*_~]*', self.pending[:limit]):
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
                boundary = end
                break
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
        return re.sub(r'\s+', ' ', text).strip()
