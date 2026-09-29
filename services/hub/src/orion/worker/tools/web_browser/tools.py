import asyncio
from typing import Literal

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable, BrowserTabMissing
from contextlib import suppress
from orion.browser.cdp import CDPConnectionLost, CDPTimeout, CDPError
from orion.browser.page import web_url
from .._base import Tool, ToolInput


class BrowserInput(ToolInput):
    action: Literal['navigate', 'read', 'click', 'type', 'scroll', 'activate']
    detailed: bool = Field(default=False,
        description='Request a larger page snapshot only when the compact read is insufficient.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    url: str | SkipJsonSchema[None] = Field(default=None,
        json_schema_extra=lambda schema: schema.pop('default', None))
    tab_id: str | SkipJsonSchema[None] = Field(default=None,
        description='MUST be an exact ID returned by browser_tabs (e.g. "A1B2"). Do NOT pass words like "new". To open a new tab or use the default background tab, completely OMIT this parameter.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    selector: str | SkipJsonSchema[None] = Field(default=None,
        description='The exact numeric `id` of the element from the snapshot (e.g., "14"), OR a custom CSS selector.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    text: str | SkipJsonSchema[None] = Field(default=None,
        json_schema_extra=lambda schema: schema.pop('default', None))
    press_enter: bool = Field(default=False,
        description='Press Enter after typing to submit',
        json_schema_extra=lambda schema: schema.pop('default', None))
    delta: int = Field(default=500,
        description='Scroll amount; use only with scroll',
        json_schema_extra=lambda schema: schema.pop('default', None))
    visible: bool = Field(default=False,
        description='If true, brings the tab to the OS foreground. Use False to keep the browser in the background (it will still stay active and receive inputs like Enter). Only use True if the user explicitly wants the window brought to the front.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    viewport_only: bool = Field(default=True,
        description='Only read elements and text visible on screen. ALWAYS leave this True to save tokens. ONLY set to False as an absolute last resort if you cannot find what you need after scrolling.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    wait_seconds: float = Field(default=0,
        description='Wait exactly this many seconds AFTER executing the action but BEFORE taking the snapshot. ALWAYS use 0-1 for navigate, read, click. ONLY use 4-8 seconds for typing into an AI so its response can stream.',
        json_schema_extra=lambda schema: schema.pop('default', None))

    @model_validator(mode='after')
    def required_fields(self):
        if self.action == 'navigate':
            web_url(self.url or '')
        if self.action in ('click', 'type') and not (self.selector or '').strip():
            raise ValueError('A selector is required')
        if self.action == 'type' and self.text is None:
            raise ValueError('Text is required')
        return self


async def browser_control(args, context):
    try:
        tab_options = {'target_id': args.tab_id} if args.tab_id else {}
        if not getattr(args, 'visible', False):
            tab_options['inactive'] = True
            
        async with BrowserManager(context.user_id).tab('browser:' + context.chat_id, **tab_options) as page:
            if getattr(args, 'visible', False):
                with suppress(Exception):
                    await page.activate()
            else:
                with suppress(Exception):
                    await page.ensure_active()
                    
            if args.action == 'navigate':
                await page.navigate(args.url)
            elif args.action == 'activate':
                await page.activate()
            elif args.action == 'click':
                await page.click(args.selector)
            elif args.action == 'type':
                await page.type_text(args.selector, args.text, press_enter=getattr(args, 'press_enter', False))
            elif args.action == 'scroll':
                await page.scroll(args.delta)
                
            if getattr(args, 'wait_seconds', 0) > 0:
                await asyncio.sleep(min(args.wait_seconds, 15))
                
            snapshot = await page.snapshot(compact=not args.detailed, viewport_only=getattr(args, 'viewport_only', True))
            if snapshot and any(k in (snapshot.get('url') or '') for k in ('/sorry/index', 'recaptcha', 'challenges.cloudflare.com')):
                try:
                    await page.activate()
                except Exception:
                    pass
                return {
                    'status': 'verification_required',
                    'page': snapshot,
                    'message': 'Sayfada robot doğrulaması (CAPTCHA / olağandışı trafik engeli) tespit edildi. Sekme ekrana getirildi; lütfen tarayıcıda doğrulamayı tamamlayın.'
                }
            return {'status': 'ok', 'page': snapshot}
    except BrowserBusy as exc:
        return {'status': 'busy', 'message': str(exc)}
    except BrowserTabMissing as exc:
        return {'status': 'tab_missing', 'message': str(exc)}
    except BrowserUnavailable as exc:
        return {'status': 'not_configured', 'message': str(exc)}
    except CDPError as exc:
        return {'status': 'unsupported_tab', 'message': str(exc)}
    except asyncio.TimeoutError:
        return {'status': 'timeout', 'message': 'Action timed out. The element might not be interactable or found.'}
    except (CDPConnectionLost, CDPTimeout):
        return {'status': 'outcome_unknown', 'message': 'Connection lost or timed out. Inspect the page before retrying any action.'}


class BrowserTabsInput(ToolInput):
    query: str | SkipJsonSchema[None] = Field(default=None,
        description='Filter tab titles and URLs; omit only when listing all tabs',
        json_schema_extra=lambda schema: schema.pop('default', None))
    include_urls: bool = Field(default=False,
        description='Include full URLs only when needed to reopen or share a page',
        json_schema_extra=lambda schema: schema.pop('default', None))


async def browser_tabs(args, context):
    try:
        tabs = await BrowserManager(context.user_id).list_tabs()
        if args.query:
            needle = args.query.casefold()
            tabs = [tab for tab in tabs if needle in tab['title'].casefold() or needle in tab['url'].casefold()]
        res = []
        for tab in tabs:
            entry = {'id': tab['id'], 'title': tab['title']}
            if args.include_urls:
                entry['url'] = tab['url']
            if '/sorry/index' in tab.get('url', '') or 'recaptcha' in tab.get('url', '').casefold():
                entry['warning'] = 'Google bot doğrulaması (CAPTCHA) sayfasında takılı'
            res.append(entry)
        return {'status': 'ok', 'tabs': res}
    except BrowserBusy as exc:
        return {'status': 'busy', 'message': str(exc)}
    except BrowserUnavailable as exc:
        return {'status': 'not_configured', 'message': str(exc)}
    except (CDPConnectionLost, CDPTimeout):
        return {'status': 'outcome_unknown', 'message': 'Could not read the connected browser tabs.'}


BROWSER_CONTROL = Tool(id='browser_control', name='Use browser',
    description='Navigate/read/click/type/scroll in the connected browser. Pass tab_id to use an existing tab; omitting it uses the default AI tab. '
                'NEVER try to use or navigate tabs that have a CAPTCHA warning. '
                'Always use detailed=True when reading chat interfaces or long pages. '
                'For clicking/typing, always use the numeric `id` provided in the elements list (e.g., selector="14"). '
                'For YouTube/Music use media_control. Omit unused arguments.',
    input_model=BrowserInput, handler=browser_control)


BROWSER_TABS = Tool(id='browser_tabs', name='List open tabs',
    description='List connected browser tabs. ONLY use this if the user explicitly asks to find a specific open tab or check existing tabs. '
                'Otherwise, prefer navigating directly using browser_control. Returns tab ID and title.',
    input_model=BrowserTabsInput, handler=browser_tabs)
