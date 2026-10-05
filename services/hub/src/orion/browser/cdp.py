"""Async raw CDP transport. Commands are never replayed automatically."""

import asyncio
import json
from contextlib import suppress
from urllib.parse import urlsplit

import aiohttp


class CDPConnectionLost(ConnectionError):
    """The command's outcome may be unknown; callers must not blindly retry."""


class CDPTimeout(TimeoutError):
    """The deadline elapsed, but the browser may have executed the command."""


class CDPError(RuntimeError):
    def __init__(self, method, error):
        self.method = method
        self.code = error.get('code')
        self.data = error.get('data')
        super().__init__(f"{method}: {error.get('message', 'CDP error')}")


class CDPSession:
    """One socket, one reader, and independently awaited command replies.

    Use one instance per asyncio event loop. Event consumers must drain `events`;
    overflow closes the connection rather than silently losing protocol events.
    Closing this transport does not close the browser or its tabs.
    """

    def __init__(self, http, socket, *, event_capacity=1024):
        self._http = http
        self._socket = socket
        self._next_id = 0
        self._pending = {}
        self._closed = False
        self.discard_events = False
        self.events = asyncio.Queue(maxsize=event_capacity)
        self._reader = asyncio.create_task(self._receive(), name='orion-cdp-reader')

    @classmethod
    async def connect(cls, url, *, timeout=10):
        parsed = urlsplit(url)
        if (parsed.scheme != 'ws' or parsed.hostname not in ('127.0.0.1', '::1')
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('CDP requires a numeric loopback WebSocket endpoint')
        http = aiohttp.ClientSession(trust_env=False)
        try:
            async with asyncio.timeout(timeout):
                socket = await http.ws_connect(url, max_msg_size=16 * 1024 * 1024, heartbeat=30)
            return cls(http, socket)
        except BaseException:
            await http.close()
            raise

    @property
    def closed(self):
        return self._closed or self._socket.closed

    async def send(self, method, params=None, *, session_id=None, timeout=10):
        if self.closed:
            raise CDPConnectionLost('CDP connection is closed')
        if timeout <= 0:
            raise ValueError('timeout must be positive')
        self._next_id += 1
        request_id = self._next_id
        message = {'id': request_id, 'method': method, 'params': params or {}}
        if session_id is not None:
            message['sessionId'] = session_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                await self._socket.send_json(message)
                reply = await future
            if 'error' in reply:
                raise CDPError(method, reply['error'])
            return reply.get('result', {})
        except TimeoutError as exc:
            raise CDPTimeout(f'{method} timed out; outcome is unknown') from exc
        except CDPConnectionLost:
            raise
        except (aiohttp.ClientError, OSError) as exc:
            raise CDPConnectionLost(f'{method}: connection lost; outcome is unknown') from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # Retrieve a concurrent disconnect exception even if sending failed.
                future.exception()

    async def _receive(self):
        reason = 'CDP connection closed; pending command outcomes are unknown'
        try:
            async for frame in self._socket:
                if frame.type == aiohttp.WSMsgType.TEXT:
                    message = json.loads(frame.data)
                    if not isinstance(message, dict):
                        raise ValueError('Invalid CDP message')
                    if 'id' in message:
                        future = self._pending.get(message['id'])
                        if future is not None and not future.done():
                            future.set_result(message)
                    elif 'method' in message:
                        if not self.discard_events:
                            self.events.put_nowait(message)
                elif frame.type == aiohttp.WSMsgType.ERROR:
                    break
        except asyncio.QueueFull:
            reason = 'CDP event queue overflow; connection closed'
        except (ValueError, TypeError, aiohttp.ClientError, OSError):
            reason = 'CDP receive failed; pending command outcomes are unknown'
        finally:
            self._closed = True
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CDPConnectionLost(reason))
            try:
                with suppress(TimeoutError, aiohttp.ClientError, OSError):
                    async with asyncio.timeout(2):
                        await self._socket.close()
            finally:
                await self._http.close()

    async def close(self):
        self._closed = True
        if not self._reader.done():
            self._reader.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader
        try:
            # The reader may have been cancelled before it entered its finally.
            with suppress(TimeoutError, aiohttp.ClientError, OSError):
                async with asyncio.timeout(2):
                    await self._socket.close()
        finally:
            await self._http.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()
