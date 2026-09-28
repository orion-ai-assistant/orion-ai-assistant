"""Small CDP page operations shared by tool handlers."""

import asyncio
import json
from urllib.parse import urlsplit

from .cdp import CDPError


def web_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('An HTTP(S) URL without credentials is required')
    return url


class BrowserPage:
    def __init__(self, connection, session_id, target_id, manager):
        self.connection, self.session_id, self.target_id = connection, session_id, target_id
        self.manager = manager

    async def send(self, method, params=None):
        return await self.connection.send(method, params, session_id=self.session_id)

    async def evaluate(self, expression, *, user_gesture=False):
        params = {'expression': expression, 'returnByValue': True, 'awaitPromise': True}
        if user_gesture:
            params['userGesture'] = True
        result = await self.send('Runtime.evaluate', params)
        if 'exceptionDetails' in result:
            raise RuntimeError('Page JavaScript evaluation failed')
        return result.get('result', {}).get('value')

    async def navigate(self, url, *, wait_ready=True):
        previous = (await self.send('Page.getFrameTree'))['frameTree']['frame'].get('loaderId')
        result = await self.send('Page.navigate', {'url': web_url(url)})
        if result.get('errorText'):
            raise RuntimeError(result['errorText'])
        if result.get('isDownload'):
            raise RuntimeError('Downloads are not supported by this browser tool')
        # A site may reload/redirect again after the requested loader commits.
        # Wait for a new document, not forever for the first loader's exact ID.
        loader = result.get('loaderId')
        async with asyncio.timeout(12):
            while True:
                tree = await self.send('Page.getFrameTree')
                if not loader or tree['frameTree']['frame'].get('loaderId') != previous:
                    if not wait_ready:
                        return
                    try:
                        if await self.evaluate("document.readyState !== 'loading'"):
                            return
                    except CDPError:
                        pass  # Execution context can disappear during a redirect.
                await asyncio.sleep(.1)

    async def require_verification(self):
        self.manager.mark_manual()
        # Verification is surfaced in Orion; only an explicit UI action opens it.

    async def click(self, selector):
        async with asyncio.timeout(8):
            while True:
                point = await self.evaluate('''(() => {
                    const el = document.querySelector(%s);
                    if (!el || el.disabled) return null;
                    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
                    const r = el.getBoundingClientRect();
                    const x = Math.max(0, r.left) + (Math.min(innerWidth, r.right) - Math.max(0, r.left))/2;
                    const y = Math.max(0, r.top) + (Math.min(innerHeight, r.bottom) - Math.max(0, r.top))/2;
                    const top = document.elementFromPoint(x, y);
                    return r.width && r.height && (top === el || el.contains(top)) ? {x,y} : null;
                })()''' % json.dumps(selector))
                if point:
                    break
                await asyncio.sleep(.15)
        for kind in ('mousePressed', 'mouseReleased'):
            await self.send('Input.dispatchMouseEvent', {'type': kind, **point, 'button': 'left', 'clickCount': 1})

    async def type_text(self, selector, text):
        await self.click(selector)
        # insertText handles Unicode and IME text without inventing physical key codes.
        await self.send('Input.insertText', {'text': text})

    async def scroll(self, delta):
        await self.send('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': 200, 'y': 200,
                                                     'deltaX': 0, 'deltaY': delta})

    async def snapshot(self, *, compact=False):
        text_limit, element_limit = (500, 10) if compact else (2500, 30)
        return await self.evaluate('''(() => ({url: location.href, title: document.title,
            text: (document.body?.innerText || '').slice(0, %d),
            text_truncated: (document.body?.innerText || '').length > %d,
            elements: [...document.querySelectorAll('a,button,input,textarea,select,[role="button"]')]
                .filter(e => e.checkVisibility()).slice(0, %d).map(e => ({tag: e.tagName,
                    id: e.id, role: e.getAttribute('role'), label: (e.getAttribute('aria-label') || e.innerText || '').slice(0, 120),
                    name: e.getAttribute('name'), href: e.href ? e.href.slice(0, 300) : null}))}))()'''
            % (text_limit, text_limit, element_limit))
