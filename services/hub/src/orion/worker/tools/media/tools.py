"""One model-facing media tool with deterministic tab and service selection."""
import asyncio
from contextlib import suppress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable
from orion.browser.cdp import CDPConnectionLost, CDPTimeout, CDPError
from .._base import Tool, ToolInput
from ..youtube.tools import YouTubeInput, youtube_control, _video_url
from ..ytmusic.tools import YTMusicInput, ytmusic_control, _ytmusic_url


class MediaInput(ToolInput):
    action: Literal['now_playing', 'status', 'play', 'pause', 'next', 'previous', 'seek',
                    'search', 'open', 'play_new']
    service: Literal['auto', 'youtube', 'ytmusic'] = Field(default='auto',
        description='Use youtube for explicit YouTube video requests, ytmusic for explicit YouTube Music requests; auto inspects playing tabs.')
    target: str | SkipJsonSchema[None] = Field(default=None,
        description='Optional tab ID, video reference, or URL. Omit for current playback.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    query: str | SkipJsonSchema[None] = Field(default=None,
        description='Title to search/play. For a music request with no named service, auto opens it in YouTube Music.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | SkipJsonSchema[None] = Field(default=None,
        description='Absolute position in seconds for seek (2 minutes = 120).',
        json_schema_extra=lambda schema: schema.pop('default', None))

    @field_validator('target', 'query', mode='before')
    @classmethod
    def empty_is_missing(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode='after')
    def check_fields(self):
        if self.action == 'seek' and self.seconds is None:
            raise ValueError('seconds is required for seek')
        if self.action == 'search' and not self.query:
            raise ValueError('query is required for search')
        if self.action in ('open', 'play_new') and not (self.query or self.target):
            raise ValueError('query or target is required')
        if self.target and len(self.target) > 2048:
            raise ValueError('target is too long')
        return self


def _service_for_url(url):
    if _ytmusic_url(url):
        return 'ytmusic'
    if _video_url(url):
        return 'youtube'
    return None


async def _media_tabs(user_id, service='auto'):
    async with BrowserManager(user_id).connected() as connection:
        targets = (await connection.send('Target.getTargets', timeout=2))['targetInfos']
        tabs = [(tab, kind) for tab in targets if tab.get('type') == 'page'
                if (kind := _service_for_url(tab.get('url', '')))
                if service == 'auto' or service == kind]
        semaphore = asyncio.Semaphore(8)

        async def inspect(tab, kind):
            async with semaphore:
                session = None
                try:
                    session = (await connection.send('Target.attachToTarget',
                        {'targetId': tab['targetId'], 'flatten': True}, timeout=2))['sessionId']
                    response = await connection.send('Runtime.evaluate', {
                        'expression': '''(() => { const v=document.querySelector('video');
                            if (!v) return null;
                            const bar=document.querySelector('ytmusic-player-bar .title');
                            const track=bar?.textContent?.trim();
                            const artist=document.querySelector('ytmusic-player-bar .byline')?.textContent?.trim();
                            const title=track ? (artist ? `${track} - ${artist}` : track) : document.title;
                            return {paused:v.paused, ended:v.ended, ready:v.readyState,
                                muted:v.muted || v.volume===0, title}; })()''',
                        'returnByValue': True}, session_id=session, timeout=2)
                    state = response.get('result', {}).get('value')
                    if 'exceptionDetails' in response:
                        return {'tab_id': tab['targetId'], 'service': kind,
                                'title': tab.get('title', ''), 'unchecked': True}
                    return {'tab_id': tab['targetId'], 'service': kind,
                            'title': (state or {}).get('title') or tab.get('title', ''),
                            'playing': bool(state and not state['paused'] and not state['ended']
                                            and state['ready'] >= 2),
                            'muted': bool(state and state['muted'])}
                except (CDPConnectionLost, CDPTimeout, CDPError, KeyError):
                    return {'tab_id': tab['targetId'], 'service': kind,
                            'title': tab.get('title', ''), 'unchecked': True}
                finally:
                    if session and not connection.closed:
                        with suppress(Exception):
                            await connection.send('Target.detachFromTarget',
                                {'sessionId': session}, timeout=2)

        return await asyncio.gather(*(inspect(tab, kind) for tab, kind in tabs))


async def media_control(args, context):
    try:
        if args.target and args.target.startswith('https://'):
            inferred = _service_for_url(args.target)
            if not inferred:
                return {'status': 'unsupported_target'}
            if args.service != 'auto' and args.service != inferred:
                return {'status': 'service_mismatch'}
            service = inferred
        else:
            service = args.service

        # A tab ID is resolved in the same scan as current playback.
        inspect_tabs = args.action in ('now_playing', 'status', 'pause', 'next', 'previous', 'seek') or (
            args.action == 'play' and not args.query) or bool(args.target and not args.target.startswith(('video:', 'https://')))
        tabs = await _media_tabs(context.user_id, service) if inspect_tabs else []
        playing = [tab for tab in tabs if tab.get('playing')]
        if args.action == 'now_playing':
            result = {'status': 'ok', 'playing': [
                {key: tab[key] for key in ('tab_id', 'service', 'title', 'muted')}
                for tab in playing]}
            if unchecked := sum(bool(tab.get('unchecked')) for tab in tabs):
                result['unchecked'] = unchecked
            return result

        target = args.target
        if target and not target.startswith(('video:', 'https://')):
            match = next((tab for tab in tabs if tab['tab_id'] == target), None)
            if not match:
                return {'status': 'tab_missing'}
            service = match['service']
        elif service == 'auto' and args.action in ('status', 'pause', 'next', 'previous', 'seek', 'play') and not (args.query or target):
            if len(playing) > 1:
                return {'status': 'ambiguous', 'playing': playing}
            if len(playing) == 1:
                target, service = playing[0]['tab_id'], playing[0]['service']
            elif any(tab.get('unchecked') for tab in tabs):
                return {'status': 'outcome_unknown', 'message': 'Some media tabs could not be inspected.'}
            elif len(tabs) == 1:
                target, service = tabs[0]['tab_id'], tabs[0]['service']
            elif tabs:
                return {'status': 'ambiguous', 'tabs': [
                    {key: tab[key] for key in ('tab_id', 'service', 'title')} for tab in tabs]}
            else:
                return {'status': 'no_tab'}
        if service == 'auto':
            service = 'ytmusic'
        model, handler = ((YTMusicInput, ytmusic_control) if service == 'ytmusic'
                          else (YouTubeInput, youtube_control))
        result = await handler(model(action=args.action, target=target,
                                     query=args.query, seconds=args.seconds), context)
        return {'service': service, **result}
    except BrowserBusy as exc:
        return {'status': 'busy', 'message': str(exc)}
    except BrowserUnavailable as exc:
        return {'status': 'not_configured', 'message': str(exc)}
    except (CDPConnectionLost, CDPTimeout):
        return {'status': 'outcome_unknown', 'message': 'Could not inspect media tabs.'}


MEDIA_CONTROL = Tool(id='media_control', name='Control media playback',
    description='One tool for YouTube and YouTube Music. For "hangi müzik çalıyor" use now_playing with service=auto; it checks BOTH services in one call. For current playback control use auto to target the playing tab. Specify service=ytmusic for explicit YouTube Music, service=youtube for explicit YouTube video requests. play+query searches and plays; unspecified service for a new song defaults to YouTube Music. seek+seconds sets exact absolute position (2:00 = 120). If status=ambiguous, use the user request and returned titles/services to select a matching tab and retry the same action with its target tab ID. For a song request, prefer a uniquely matching music tab over an unrelated video. Ask the user only if the intended target remains unclear. Only report success when status=ok; explain failures or unverified outcomes from the returned result. Results verify actions; avoid extra status/read calls.',
    input_model=MediaInput, handler=media_control, timeout_seconds=55)
