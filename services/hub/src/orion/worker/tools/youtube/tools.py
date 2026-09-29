import asyncio
import re
from contextlib import suppress
from time import monotonic
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from pydantic import Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from orion.browser.manager import BrowserManager, BrowserBusy, BrowserUnavailable, BrowserTabMissing
from orion.browser.cdp import CDPConnectionLost, CDPTimeout, CDPError
from .._base import Tool, ToolInput
from .._media import compact_state, seek, skip


class YouTubeInput(ToolInput):
    action: Literal['search', 'open', 'play', 'play_new', 'pause', 'next', 'previous', 'seek', 'status', 'now_playing']
    query: str | SkipJsonSchema[None] = Field(default=None,
        description='Video title to search and play; also accepts a video reference.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    target: str | SkipJsonSchema[None] = Field(default=None,
        description='Tab ID, video reference, or standard YouTube URL. Combine a tab ID with query to replace its video.',
        json_schema_extra=lambda schema: schema.pop('default', None))

    seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | SkipJsonSchema[None] = Field(default=None,
        description='Absolute playback position in seconds; required for seek (2 minutes = 120).',
        json_schema_extra=lambda schema: schema.pop('default', None))

    @field_validator('query', 'target', mode='before')
    @classmethod
    def empty_is_missing(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode='after')
    def required_fields(self):
        if self.action == 'seek' and self.seconds is None:
            raise ValueError('seconds is required for seek')
        if self.action == 'seek' and (self.query or (self.target and ':' in self.target)):
            raise ValueError('seek accepts only an optional tab ID and seconds')
        if self.target and len(self.target) > 2048:
            raise ValueError('target is too long')
        if self.action == 'search' and not (self.query or '').strip():
            raise ValueError('A query is required')
        if self.action in ('open', 'play_new') and not self.target and not (self.query or '').strip():
            raise ValueError('A target or search query is required')
        if self.action == 'play_new' and self.target and not (_video_ref(self.target) or _youtube_url(self.target)):
            raise ValueError('play_new needs a video reference or YouTube URL, not a tab ID')
        if self.target and ':' in self.target and not (_video_ref(self.target) or _youtube_url(self.target)):
            raise ValueError('A YouTube HTTPS URL is required')
        return self


def _youtube_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname in
            ('www.youtube.com', 'youtube.com', 'youtu.be')
            and not parsed.username and not parsed.password)


def _video_id(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', value))


def _video_ref(value):
    video_id = value.removeprefix('video:') if value else ''
    return video_id if value and value.startswith('video:') and _video_id(video_id) else None


class MusicIsPlaying(Exception):
    pass


async def _find_existing_youtube_tab(user_id, *, guard_music=False):
    """Tarayıcıda açık YouTube video sekmesini bulur."""
    try:
        manager = BrowserManager(user_id)
        async with manager.connected() as connection:
            targets = (await connection.send('Target.getTargets', timeout=2))['targetInfos']
            yt_tabs = [t for t in targets if t.get('type') == 'page' and _video_url(t.get('url', ''))]
            music_tabs = [t for t in targets if t.get('type') == 'page'
                          and urlsplit(t.get('url', '')).hostname == 'music.youtube.com'] if guard_music else []
            if not yt_tabs and not music_tabs:
                return None
            for tab in yt_tabs + music_tabs:
                session = None
                try:
                    session = (await connection.send('Target.attachToTarget',
                        {'targetId': tab['targetId'], 'flatten': True}, timeout=2))['sessionId']
                    resp = await connection.send('Runtime.evaluate', {
                        'expression': '(() => { const v=document.querySelector("video"); return v && !v.paused && !v.ended; })()',
                        'returnByValue': True
                    }, session_id=session, timeout=2)
                    if resp.get('result', {}).get('value'):
                        if tab in music_tabs:
                            raise MusicIsPlaying()
                        return tab['targetId']
                except MusicIsPlaying:
                    raise
                except Exception:
                    pass
                finally:
                    if session and not connection.closed:
                        with suppress(Exception):
                            await connection.send('Target.detachFromTarget', {'sessionId': session}, timeout=2)
            return yt_tabs[0]['targetId'] if yt_tabs else None
    except MusicIsPlaying:
        raise
    except Exception:
        return None


async def youtube_control(args, context):
    tab_id = None
    try:
        if args.action == 'now_playing':
            return await _now_playing(context.user_id)
        video_id = _video_ref(args.target)
        target_url = (('https://www.youtube.com/watch?v=' + video_id) if video_id else
                      args.target if args.target and _youtube_url(args.target) else None)
        tab_id = args.target if args.target and not target_url and args.action != 'search' else None
        query_video = _video_ref(args.query) or (args.query if args.query and _video_id(args.query) else None)
        selected_url = target_url
        if not selected_url and args.action != 'search' and query_video:
            selected_url = 'https://www.youtube.com/watch?v=' + query_video

        # Hedef sekme belirtilmemişse tarayıcıda zaten açık olan YouTube sekmesini bul
        if not tab_id and args.action != 'play_new':
            tab_id = await _find_existing_youtube_tab(context.user_id,
                guard_music=args.action in ('seek', 'pause', 'next', 'previous'))

        can_open = args.action in ('search', 'open', 'play_new') or (args.action == 'play' and bool(args.query or selected_url))
        tab_options = {'target_id': tab_id} if tab_id else {'inactive': True, 'create': can_open}
        tag = ('youtube:' + uuid4().hex if args.action == 'play_new' else
               'youtube-search' if args.action == 'search' or video_id else 'youtube')
        async with BrowserManager(context.user_id).tab(tag, **tab_options) as page:
            if hasattr(page, 'bypass_beforeunload'):
                await page.bypass_beforeunload()
            if tab_id and not _youtube_url(await page.evaluate('location.href')):
                return {'status': 'unsupported_tab', 'message': 'Seçilen sekme YouTube sayfası değil.'}
            if args.action in ('search', 'open', 'play', 'play_new'):
                # Chromium can defer loading and media startup in an inactive
                # tab. Emulate page focus without activating the browser tab.
                await page.send('Emulation.setFocusEmulationEnabled', {'enabled': True})
            needs_open = args.action in ('open', 'play_new') or (args.action == 'play' and bool(selected_url or args.query))
            if args.action == 'search' or (needs_open and not selected_url):
                if hasattr(page, 'bypass_beforeunload'):
                    await page.bypass_beforeunload()
                await page.navigate('https://www.youtube.com/results?' + urlencode({'search_query': args.query}),
                                    wait_ready=False)
                async with asyncio.timeout(8):
                    while True:
                        results = await page.evaluate('''(() => {
                            const ytItems = [...document.querySelectorAll('a#video-title')];
                            if (ytItems.length > 0) {
                                return ytItems.map(e => ({
                                    title: e.textContent.trim(),
                                    target: 'video:' + new URL(e.href, location.origin).searchParams.get('v')
                                })).filter(e => e.title && /^video:[A-Za-z0-9_-]{11}$/.test(e.target)).slice(0, 5);
                            }
                            return [];
                        })()''')
                        if results:
                            if args.action == 'search':
                                return {'status': 'ok', 'results': results}
                            selected = next((item for item in results if _video_ref(item.get('target'))), None)
                            if selected:
                                selected_url = 'https://www.youtube.com/watch?v=' + _video_ref(selected['target'])
                                break
                        if await _verification(page):
                            return {'status': 'verification_required', 'message': 'Complete sign-in/consent in your connected browser, then resume in settings.'}
                        await asyncio.sleep(.3)
            if needs_open:
                if hasattr(page, 'bypass_beforeunload'):
                    await page.bypass_beforeunload()
                await page.navigate(selected_url, wait_ready=False)
            if await _verification(page):
                return {'status': 'verification_required', 'message': 'Complete sign-in/consent in your connected browser, then resume in settings.'}
            async with asyncio.timeout(8):
                while not await page.evaluate('!!document.querySelector("video")'):
                    if args.action in ('status', 'play', 'pause', 'next', 'previous', 'seek') and not needs_open:
                        return {'status': 'no_video', 'tab_id': page.target_id}
                    await asyncio.sleep(.2)
            if args.action == 'pause':
                paused = await page.evaluate('document.querySelector("video").paused')
                if not paused:
                    await page.evaluate('document.querySelector("video").pause()')
                async with asyncio.timeout(5):
                    while not await page.evaluate('document.querySelector("video").paused'):
                        await asyncio.sleep(.1)
                return _playback_result(await _playback_state(page), page.target_id)
            if args.action == 'seek':
                return await seek(page, args.seconds, _playback_state)
            if args.action in ('next', 'previous'):
                return await skip(page, args.action, _playback_state, _playback_result, music=False)

            if args.action in ('open', 'play', 'play_new'):
                play_result = await _play_in_background(page) or {}
                if not await _time_advanced(page, 3):
                    return {'status': 'playback_unconfirmed', **compact_state(await _playback_state(page), page.target_id),
                            'tab_id': page.target_id,
                            'play_error': play_result.get('error'),
                            'message': 'Arka planda oynatma başlamadı. Brave otomatik oynatmayı engelliyor olabilir.'}
                return _playback_result(await _playback_state(page), page.target_id)
            state = await _playback_state(page)
            if not state['paused'] and not await _time_advanced(page, 2):
                return {'status': 'playback_unconfirmed', **compact_state(state, page.target_id),
                        'message': 'Video zamanı ilerlemiyor; oynatma doğrulanamadı.'}
            return _playback_result(state, page.target_id, detailed=True)
    except MusicIsPlaying:
        return {'status': 'wrong_media_tool', 'tool': 'ytmusic_control',
                'message': 'YouTube Music is playing; no YouTube video was changed. Use ytmusic_control with the same action.'}
    except BrowserBusy as exc:
        return {'status': 'busy', 'message': str(exc)}
    except BrowserTabMissing as exc:
        return {'status': 'tab_missing' if tab_id else 'no_video', 'message': str(exc)}
    except BrowserUnavailable as exc:
        return {'status': 'not_configured', 'message': str(exc)}
    except (CDPConnectionLost, CDPTimeout) as exc:
        return {'status': 'outcome_unknown', 'reason': str(exc),
                'message': 'Tarayıcı komutu yanıt vermedi. YouTube sekmesinin durumunu kontrol edin.'}
    except TimeoutError:
        return {'status': 'timeout', 'message': 'YouTube did not reach the expected state. Inspect its tab before retrying.'}


async def _verification(page):
    url = await page.evaluate('location.href')
    if urlsplit(url).hostname in ('accounts.google.com', 'consent.youtube.com', 'consent.google.com'):
        await page.require_verification()
        return True
    return False


async def _playback_state(page):
    return await page.evaluate('''(() => {
        const v = document.querySelector('video');
        if (!v) return null;
        let title = document.title;
        const ytMusicTitle = document.querySelector('ytmusic-player-bar .title');
        const ytMusicArtist = document.querySelector('ytmusic-player-bar .byline');
        if (ytMusicTitle && ytMusicTitle.textContent.trim()) {
            const track = ytMusicTitle.textContent.trim();
            const artist = ytMusicArtist ? ytMusicArtist.textContent.trim() : '';
            title = artist ? `${track} - ${artist} | YouTube Music` : `${track} | YouTube Music`;
        }
        return {
            url: location.href,
            title: title,
            paused: v.paused,
            current_time: v.currentTime,
            duration: Number.isFinite(v.duration) ? v.duration : null,
            muted: v.muted,
            volume: v.volume,
            ready_state: v.readyState,
            error: v.error ? v.error.message : null,
            ad_showing: !!document.querySelector('.ad-showing, .ytp-ad-player-overlay')
        };
    })()''')


async def _play_in_background(page):
    return await page.evaluate('''(() => {
        const v = document.querySelector('video');
        if (!v) return {ok:false, error:'Video not found'};
        return v.play().then(() => ({ok:true}), e => ({ok:false, error:e.name || String(e)}));
    })()''', user_gesture=True)


async def _now_playing(user_id):
    manager = BrowserManager(user_id)
    async with manager.connected() as connection:
        targets = (await connection.send('Target.getTargets', timeout=2))['targetInfos']
        tabs = [tab for tab in targets if tab.get('type') == 'page' and _video_url(tab.get('url', ''))]
        semaphore = asyncio.Semaphore(8)

        async def inspect(tab):
            async with semaphore:
                session = None
                try:
                    session = (await connection.send('Target.attachToTarget',
                        {'targetId': tab['targetId'], 'flatten': True}, timeout=2))['sessionId']
                    response = await connection.send('Runtime.evaluate', {
                        'expression': '''(() => { const v=document.querySelector('video');
                            return v && {paused:v.paused, ended:v.ended, ready:v.readyState,
                                         muted:v.muted || v.volume===0}; })()''',
                        'returnByValue': True}, session_id=session, timeout=2)
                    if 'exceptionDetails' in response:
                        return None, True
                    state = response.get('result', {}).get('value')
                    if state and not state['paused'] and not state['ended'] and state['ready'] >= 2:
                        return {'tab_id': tab['targetId'], 'title': tab.get('title', ''),
                                'muted': state['muted']}, False
                    return None, False
                except (CDPConnectionLost, CDPTimeout, CDPError, KeyError):
                    return None, True
                finally:
                    if session and not connection.closed:
                        with suppress(Exception):
                            await connection.send('Target.detachFromTarget',
                                {'sessionId': session}, timeout=2)

        states = await asyncio.gather(*(inspect(tab) for tab in tabs))
    playing = [entry for entry, _ in states if entry]
    unchecked = sum(failed for _, failed in states)
    result = {'status': 'ok', 'playing': playing}
    if unchecked:
        result['unchecked'] = unchecked
    return result


def _video_url(url):
    parsed = urlsplit(url)
    return (_youtube_url(url) and
            (parsed.path == '/watch' or parsed.path.startswith(('/shorts/', '/live/'))
             or parsed.hostname in ('youtu.be', 'music.youtube.com')))


def _playback_result(state, tab_id, *, detailed=False):
    result = compact_state(state, tab_id)
    if state['paused']:
        return {'status': 'ok', **result}
    if state['ad_showing']:
        return {'status': 'ad_playing', **result,
                'message': 'YouTube reklam oynatıyor; istenen videonun başladığı henüz doğrulanmadı.'}
    if state['muted'] or state['volume'] == 0:
        return {'status': 'muted', **result,
                'message': 'Video ilerliyor ama oynatıcı sessizde veya sesi sıfır.'}
    return {'status': 'ok', **result}


async def _time_advanced(page, seconds):
    start = await page.evaluate('document.querySelector("video").currentTime')
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        await asyncio.sleep(.25)
        current = await page.evaluate('document.querySelector("video").currentTime')
        if current is not None and start is not None and current - start >= .25:
            return True
    return False


YOUTUBE_CONTROL = Tool(id='youtube_control', name='Control YouTube playback',
    description='Control standard YouTube VIDEOS. For music/song requests (müzik/şarkı), default to ytmusic_control even if the service is not named. '
                'play+query searches and plays; play without query resumes. next/previous skip; seek+seconds jumps to a position. '
                'Omit target to find the open/playing tab automatically; no browser_tabs or status preflight needed. '
                'Results verify the action; do not recheck successful actions. Use status only when asked. '
                'search lists candidates; play_new opens a separate tab only when requested. Omit unused fields.',
    input_model=YouTubeInput, handler=youtube_control, timeout_seconds=55)
