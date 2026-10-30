"""First sentence immediately, then bounded batches of whatever is ready.

A sentence under MIN_WORDS ("Selam.") is not spoken alone: it waits for the
next one. The reply's end sends whatever is left, so a short reply still speaks.
"""
import asyncio
from collections import deque
from contextlib import suppress

from orion.worker.services.sentence_segmenter import SentenceSegmenter
from orion.worker.services.tts_stream import relay_tts_batches


class AdaptiveTts:
    MAX_QUEUED_CHARS = 12_000
    MAX_BATCH_SENTENCES = 3
    MAX_BATCH_CHARS = 600
    MIN_WORDS = 3

    def __init__(self, context, settings, should_stop, arrival_ms):
        self.segmenter = SentenceSegmenter()
        self.held = ""
        self.queue = deque()
        self.queued_chars = 0
        self.condition = asyncio.Condition()
        self.closed = False
        self.error = None
        self.source = ""
        self.committed = ""
        self.task = asyncio.create_task(self._run(context, settings, should_stop, arrival_ms))

    async def _run(self, *args):
        try:
            return await relay_tts_batches(*args, self.groups())
        except Exception as error:
            self.error = error
            return None  # The text response continues; never resynthesize it.
        finally:
            async with self.condition:
                self.closed = True
                self.queue.clear()
                self.queued_chars = 0
                self.condition.notify_all()

    async def feed(self, text, snapshot=False):
        try:
            await self._feed(text, snapshot)
        except Exception as error:
            self.error = error
            await self.cancel()

    async def _feed(self, text, snapshot=False):
        if self.closed:
            return
        if len(text) + (0 if snapshot else len(self.source)) > 120_000:
            raise ValueError("Speech response text exceeds limit")
        if snapshot:
            if not text.startswith(self.committed):
                self.error = ValueError("Model revised text already queued for speech")
                await self.cancel()
                return
            self.source = text
            self.segmenter.pending = text[len(self.committed):]
            pieces = self.segmenter.feed("")
        else:
            self.source += text
            pieces = self.segmenter.feed(text)
        self.committed = self.source[:len(self.source) - len(self.segmenter.pending)]
        await self._put(self._join_short(pieces))

    def _join_short(self, pieces, final=False):
        result = []
        for piece in pieces:
            if self.held:
                piece = f"{self.held} {piece}"
                self.held = ""
            if len(piece.split()) < self.MIN_WORDS:
                self.held = piece
            else:
                result.append(piece)
        if final and self.held:
            result.append(self.held)
            self.held = ""
        return result

    async def _put(self, pieces):
        for piece in pieces:
            async with self.condition:
                await self.condition.wait_for(lambda: self.closed or self.queued_chars + len(piece) <= self.MAX_QUEUED_CHARS)
                if self.closed:
                    return
                self.queue.append(piece)
                self.queued_chars += len(piece)
                self.condition.notify_all()

    async def groups(self):
        first = True
        index = 0
        while True:
            async with self.condition:
                await self.condition.wait_for(lambda: self.queue or self.closed)
                if not self.queue:
                    return
                batch = [self.queue.popleft()]
                length = len(batch[0])
                while not first and self.queue and len(batch) < self.MAX_BATCH_SENTENCES:
                    if length + 1 + len(self.queue[0]) > self.MAX_BATCH_CHARS:
                        break
                    part = self.queue.popleft()
                    batch.append(part)
                    length += 1 + len(part)
                self.queued_chars -= sum(map(len, batch))
                self.condition.notify_all()
            yield index, " ".join(batch)
            index += len(batch)
            first = False

    async def finish(self):
        if not self.closed:
            await self._put(self._join_short(self.segmenter.feed("", final=True), final=True))
            async with self.condition:
                self.closed = True
                self.condition.notify_all()
        return await self.task

    async def cancel(self):
        async with self.condition:
            self.closed = True
            self.queue.clear()
            self.condition.notify_all()
        if not self.task.done():
            self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
