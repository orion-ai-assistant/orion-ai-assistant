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

    async def handle_dialog(self, accept=True):
        """Açık olan herhangi bir JavaScript dialogunu (beforeunload, alert, confirm) onaylar veya kapatır."""
        if not self.connection:
            return
        try:
            await self.send('Page.enable')
        except Exception:
            pass
        try:
            await self.send('Page.handleJavaScriptDialog', {'accept': accept})
        except Exception:
            pass

    async def bypass_beforeunload(self):
        """Sayfanın 'Siteden çıkılsın mı?' (beforeunload) uyarısı vermesini engeller ve varsa açık dialogu onaylar."""
        if not self.connection:
            return
        await self.handle_dialog(accept=True)
        try:
            await self.evaluate('''(() => {
                try {
                    window.onbeforeunload = null;
                    window.onunload = null;
                    const stop = (e) => {
                        delete e.returnValue;
                        e.preventDefault = () => {};
                        e.stopImmediatePropagation();
                    };
                    window.addEventListener('beforeunload', stop, {capture: true});
                } catch(_) {}
            })()''')
        except Exception:
            pass

    async def navigate(self, url, *, wait_ready=True, bypass_dialog=True):
        if bypass_dialog:
            await self.bypass_beforeunload()
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
                    except Exception:
                        pass  # Execution context can disappear during a redirect or captcha challenge.
                elif bypass_dialog:
                    try:
                        await self.send('Page.handleJavaScriptDialog', {'accept': True})
                    except Exception:
                        pass
                await asyncio.sleep(.1)

    async def require_verification(self):
        self.manager.mark_manual()
        # Verification is surfaced in Orion; only an explicit UI action opens it.

    async def click(self, selector):
        async with asyncio.timeout(8):
            while True:
                point = await self.evaluate('''(() => {
                    let sel = %s;
                    if (/^\d+$/.test(sel)) {
                        sel = '[data-orion-id="' + sel + '"]';
                    }
                    const el = document.querySelector(sel);
                    if (!el || el.disabled) return null;
                    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
                    const r = el.getBoundingClientRect();
                    const x = Math.max(0, r.left) + (Math.min(innerWidth, r.right) - Math.max(0, r.left))/2;
                    const y = Math.max(0, r.top) + (Math.min(innerHeight, r.bottom) - Math.max(0, r.top))/2;
                    const top = document.elementFromPoint(x, y);
                    if (!r.width || !r.height) return null;
                    if (top === el || el.contains(top)) return {x,y};
                    const isFocusable = ['INPUT', 'TEXTAREA', 'BUTTON', 'SELECT'].includes(el.tagName) || el.isContentEditable || el.hasAttribute('role');
                    return isFocusable ? {x,y} : null;
                })()''' % json.dumps(selector))
                if point:
                    break
                await asyncio.sleep(.15)
        for kind in ('mousePressed', 'mouseReleased'):
            await self.send('Input.dispatchMouseEvent', {'type': kind, **point, 'button': 'left', 'clickCount': 1})

    async def activate(self):
        await self.send('Page.bringToFront')

    async def ensure_active(self):
        """Arka plandaki sekmenin donmasını engeller ve odaklanmış gibi davranmasını sağlar."""
        try:
            await self.send('Page.enable')
            await self.send('Page.setWebLifecycleState', {'state': 'active'})
        except Exception:
            pass
        try:
            await self.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
        except Exception:
            pass

    async def type_text(self, selector, text, press_enter=False):
        await self.click(selector)
        # insertText handles Unicode and IME text without inventing physical key codes.
        await self.send('Input.insertText', {'text': text})
        if press_enter:
            await self.send('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Enter', 'code': 'Enter', 'text': '\r', 'unmodifiedText': '\r', 'windowsVirtualKeyCode': 13})
            await self.send('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': 'Enter', 'code': 'Enter', 'windowsVirtualKeyCode': 13})
            await asyncio.sleep(1.0)

    async def scroll(self, delta):
        await self.send('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': 200, 'y': 200,
                                                     'deltaX': 0, 'deltaY': delta})

    async def snapshot(self, *, compact=False, viewport_only=True):
        text_limit, element_limit = (500, 15) if compact else (3000, 30)
        
        js_code = '''((viewportOnly, textLimit, elementLimit) => {
            const isVisible = (el) => {
                if (!el.checkVisibility()) return false;
                if (!viewportOnly) return true;
                const r = el.getBoundingClientRect();
                return (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth);
            };

            // 1. Text Extraction: Prioritize main content area
            let text = '';
            const mainEl = document.querySelector('main, [role="main"], article');
            if (mainEl) {
                text = mainEl.innerText || '';
            } else {
                text = document.body?.innerText || '';
            }
            text = text.replace(/\\s+/g, ' ').trim();
            const textTruncated = text.length > textLimit;
            text = text.slice(0, textLimit);

            // 2. Elements Extraction
            const rawElements = [...document.querySelectorAll('a, button, input, textarea, select, [role="button"], [role="textbox"], [contenteditable="true"], rich-textarea')]
                .filter(isVisible);

            // Assign unique ID to every visible interactive element
            let idCounter = 1;
            rawElements.forEach(e => {
                e.setAttribute('data-orion-id', idCounter++);
            });

            // Filter out elements that have no label and aren't inputs (e.g. empty decorative wrappers)
            const elements = rawElements.filter(e => {
                const isInput = ['INPUT', 'TEXTAREA'].includes(e.tagName) || e.hasAttribute('contenteditable') || e.getAttribute('role') === 'textbox' || e.tagName.toLowerCase() === 'rich-textarea';
                if (isInput) return true;
                const label = (e.getAttribute('aria-label') || e.innerText || e.getAttribute('title') || '').trim();
                return label.length > 0;
            });

            const inputs = elements.filter(e => ['INPUT', 'TEXTAREA'].includes(e.tagName) || e.hasAttribute('contenteditable') || e.getAttribute('role') === 'textbox' || e.tagName.toLowerCase() === 'rich-textarea');
            const buttonsLinks = elements.filter(e => !inputs.includes(e));
            const topElements = [...inputs.slice(0, 15), ...buttonsLinks].slice(0, elementLimit);

            return {
                url: location.href,
                title: document.title,
                text: text,
                text_truncated: textTruncated,
                elements: topElements.map(e => ({
                    id: e.getAttribute('data-orion-id'),
                    tag: e.tagName,
                    role: e.getAttribute('role') || undefined,
                    name: e.getAttribute('name') || undefined,
                    type: e.getAttribute('type') || undefined,
                    placeholder: e.getAttribute('placeholder') || undefined,
                    label: (e.getAttribute('aria-label') || e.innerText || e.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 40) || undefined,
                    href: e.href ? e.href.slice(0, 150) : undefined
                }))
            };
        })(%s, %d, %d)''' % ('true' if viewport_only else 'false', text_limit, element_limit)
        
        return await self.evaluate(js_code)
