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


class YTMusicInput(ToolInput):
    action: Literal['search', 'open', 'play', 'play_new', 'pause', 'next', 'previous', 'seek', 'status', 'now_playing']
    query: str | SkipJsonSchema[None] = Field(default=None,
        description='Song name, artist, album or playlist to search and play on YouTube Music; also accepts a video reference.',
        json_schema_extra=lambda schema: schema.pop('default', None))
    target: str | SkipJsonSchema[None] = Field(default=None,
        description='Tab ID, video reference (video:ID), or YouTube Music URL (https://music.youtube.com/...). Omit to automatically use the active YouTube Music tab.',
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
            raise ValueError('A search query is required')
        if self.action in ('open', 'play_new') and not self.target and not (self.query or '').strip():
            raise ValueError('A target or query is required')
        if self.action == 'play_new' and self.target and not (_video_ref(self.target) or _ytmusic_url(self.target)):
            raise ValueError('play_new needs a video reference or YouTube Music URL, not a tab ID')
        if self.target and ':' in self.target and not (_video_ref(self.target) or _ytmusic_url(self.target)):
            raise ValueError('A YouTube Music HTTPS URL is required')
        return self


def _ytmusic_url(url):
    if not url:
        return False
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname == 'music.youtube.com'
            and not parsed.username and not parsed.password)


def _video_id(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', value))


def _video_ref(value):
    video_id = value.removeprefix('video:') if value else ''
    return video_id if value and value.startswith('video:') and _video_id(video_id) else None


async def _find_existing_ytmusic_tab(user_id):
    """Tarayıcıda kullanıcı veya sistem tarafından açılmış YouTube Music sekmesini bulur.
    Öncelik: Şu an ses/müzik çalan sekme.
    İkincil: Herhangi bir açık music.youtube.com sekmesi.
    """
    try:
        manager = BrowserManager(user_id)
        async with manager.connected() as connection:
            targets = (await connection.send('Target.getTargets', timeout=2))['targetInfos']
            music_tabs = [t for t in targets if t.get('type') == 'page' and _ytmusic_url(t.get('url', ''))]
            if not music_tabs:
                return None

            # 1. Çalan sekmeyi kontrol et
            for tab in music_tabs:
                session = None
                try:
                    session = (await connection.send('Target.attachToTarget',
                        {'targetId': tab['targetId'], 'flatten': True}, timeout=2))['sessionId']
                    resp = await connection.send('Runtime.evaluate', {
                        'expression': '(() => { const v=document.querySelector("video"); return v && !v.paused && !v.ended; })()',
                        'returnByValue': True
                    }, session_id=session, timeout=2)
                    if resp.get('result', {}).get('value'):
                        return tab['targetId']
                except Exception:
                    pass
                finally:
                    if session and not connection.closed:
                        with suppress(Exception):
                            await connection.send('Target.detachFromTarget', {'sessionId': session}, timeout=2)

            # 2. Çalan yoksa ilk açık YouTube Music sekmesini seç
            return music_tabs[0]['targetId']
    except Exception:
        return None


async def ytmusic_control(args, context):
    tab_id = None
    try:
        if args.action == 'now_playing':
            return await _now_playing(context.user_id)

        video_id = _video_ref(args.target)
        target_url = (('https://music.youtube.com/watch?v=' + video_id) if video_id else
                      args.target if args.target and _ytmusic_url(args.target) else None)
        tab_id = args.target if args.target and not target_url and args.action != 'search' else None

        query_video = _video_ref(args.query) or (args.query if args.query and _video_id(args.query) else None)
        selected_url = target_url
        if not selected_url and args.action != 'search' and query_video:
            selected_url = 'https://music.youtube.com/watch?v=' + query_video

        # Eğer açık bir sekme belirtilmediyse, tarayıcıda zaten açık olan YouTube Music sekmesini bul
        if not tab_id and args.action != 'play_new':
            tab_id = await _find_existing_ytmusic_tab(context.user_id)

        can_open = args.action in ('search', 'open', 'play_new') or (args.action == 'play' and bool(args.query or selected_url))
        if not tab_id and not can_open:
            return {'status': 'no_tab', 'message': 'Açık bir YouTube Music sekmesi bulunamadı. Şarkı adı belirterek başlatabilirsiniz.'}

        tab_options = {'target_id': tab_id} if tab_id else {'inactive': True, 'create': can_open}
        tag = ('ytmusic:' + uuid4().hex if args.action == 'play_new' else
               'ytmusic-search' if args.action == 'search' else 'ytmusic')

        async with BrowserManager(context.user_id).tab(tag, **tab_options) as page:
            # Sekmeye bağlanır bağlanmaz açık olan popup'ı kapat ve beforeunload'ı devre dışı bırak
            if hasattr(page, 'bypass_beforeunload'):
                await page.bypass_beforeunload()

            current_href = await page.evaluate('location.href')
            if tab_id and not _ytmusic_url(current_href) and current_href != 'about:blank':
                return {'status': 'unsupported_tab', 'message': 'Seçilen sekme YouTube Music sayfası değil.'}

            if args.action in ('search', 'open', 'play', 'play_new'):
                await page.send('Emulation.setFocusEmulationEnabled', {'enabled': True})

            needs_open = args.action in ('open', 'play_new') or (args.action == 'play' and bool(selected_url or args.query))

            # Arama veya Oynatılacak Parçayı Arama
            if args.action == 'search' or (needs_open and not selected_url):
                search_url = 'https://music.youtube.com/search?' + urlencode({'q': args.query})
                if hasattr(page, 'bypass_beforeunload'):
                    await page.bypass_beforeunload()
                await page.navigate(search_url, wait_ready=False)
                async with asyncio.timeout(8):
                    while True:
                        results = await page.evaluate('''(() => {
                            const items = [...document.querySelectorAll('ytmusic-responsive-list-item-renderer, ytmusic-two-row-item-renderer')];
                            return items.map(e => {
                                const link = e.querySelector('a[href*="watch?v="]');
                                if (!link) return null;
                                const v = new URL(link.href, location.origin).searchParams.get('v');
                                const titleEl = e.querySelector('.title, yt-formatted-string.title') || link;
                                const bylineEl = e.querySelector('.byline, yt-formatted-string.byline');
                                const track = titleEl ? titleEl.textContent.trim() : '';
                                const artist = bylineEl ? bylineEl.textContent.trim() : '';
                                const fullTitle = artist ? `${track} - ${artist}` : track;
                                return (track && v && /^[A-Za-z0-9_-]{11}$/.test(v)) ? {title: fullTitle, target: 'video:' + v} : null;
                            }).filter(Boolean).slice(0, 5);
                        })()''')
                        if results:
                            if args.action == 'search':
                                return {'status': 'ok', 'results': results}
                            selected = next((item for item in results if _video_ref(item.get('target'))), None)
                            if selected:
                                selected_url = 'https://music.youtube.com/watch?v=' + _video_ref(selected['target'])
                                break
                        if await _verification(page):
                            return {'status': 'verification_required', 'message': 'Complete sign-in/consent in your connected browser, then resume in settings.'}
                        await asyncio.sleep(.3)

            if needs_open:
                if not selected_url:
                    selected_url = 'https://music.youtube.com/'
                if hasattr(page, 'bypass_beforeunload'):
                    await page.bypass_beforeunload()
                await page.navigate(selected_url, wait_ready=False)

            if await _verification(page):
                return {'status': 'verification_required', 'message': 'Complete sign-in/consent in your connected browser, then resume in settings.'}

            async with asyncio.timeout(8):
                while not await page.evaluate('!!document.querySelector("video")'):
                    if args.action in ('status', 'pause', 'next', 'previous', 'seek') and not needs_open:
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
                return await seek(page, args.seconds, _playback_state, music=True)
            if args.action in ('next', 'previous'):
                return await skip(page, args.action, _playback_state, _playback_result, music=True)

            if args.action in ('open', 'play', 'play_new'):
                play_result = await _play_in_background(page) or {}
                if not await _time_advanced(page, 3):
                    return {'status': 'playback_unconfirmed', **compact_state(await _playback_state(page), page.target_id),
                            'tab_id': page.target_id,
                            'play_error': play_result.get('error'),
                            'message': 'Arka planda oynatma başlamadı. Tarayıcının otomatik oynatma izinlerini kontrol edin.'}
                return _playback_result(await _playback_state(page), page.target_id)

            state = await _playback_state(page)
            if not state['paused'] and not await _time_advanced(page, 2):
                return {'status': 'playback_unconfirmed', **compact_state(state, page.target_id),
                        'message': 'Müzik süresi ilerlemiyor; oynatma doğrulanamadı.'}
            return _playback_result(state, page.target_id, detailed=True)

    except BrowserBusy as exc:
        return {'status': 'busy', 'message': str(exc)}
    except BrowserTabMissing as exc:
        return {'status': 'tab_missing' if tab_id else 'no_video', 'message': str(exc)}
    except BrowserUnavailable as exc:
        return {'status': 'not_configured', 'message': str(exc)}
    except (CDPConnectionLost, CDPTimeout) as exc:
        return {'status': 'outcome_unknown', 'reason': str(exc),
                'message': 'Tarayıcı komutu yanıt vermedi. YouTube Music sekmesinin durumunu kontrol edin.'}
    except TimeoutError:
        return {'status': 'timeout', 'message': 'YouTube Music beklenen duruma ulaşamadı. Sekmeyi kontrol edip tekrar deneyin.'}


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
        const clock = text => {
            const parts = (text || '').trim().split(':').map(Number);
            return parts.length >= 2 && parts.length <= 3 && parts.every(Number.isFinite)
                ? parts.reduce((total, part) => total * 60 + part, 0) : null;
        };
        const displayed = (document.querySelector('ytmusic-player-bar .time-info')?.textContent || '').split('/');
        const player = document.querySelector('#movie_player');
        const apiTime = typeof player?.getCurrentTime === 'function' ? player.getCurrentTime() : null;
        const apiDuration = typeof player?.getDuration === 'function' ? player.getDuration() : null;
        const uiTime = displayed.length === 2 ? clock(displayed[0]) : null;
        const uiDuration = displayed.length === 2 ? clock(displayed[1]) : null;
        return {
            url: location.href,
            title: title,
            paused: v.paused,
            current_time: Number.isFinite(apiTime) ? apiTime : (uiTime ?? v.currentTime),
            duration: uiDuration ?? (Number.isFinite(apiDuration) ? apiDuration :
                (Number.isFinite(v.duration) ? v.duration : null)),
            player_time: Number.isFinite(apiTime) ? apiTime : null,
            player_state: typeof player?.getPlayerState === 'function' ? player.getPlayerState() : null,
            ui_current_time: uiTime,
            ui_duration: uiDuration,
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
        tabs = [tab for tab in targets if tab.get('type') == 'page' and _ytmusic_url(tab.get('url', ''))]
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


def _playback_result(state, tab_id, *, detailed=False):
    result = compact_state(state, tab_id)
    if state['paused']:
        return {'status': 'ok', **result}
    if state['ad_showing']:
        return {'status': 'ad_playing', **result,
                'message': 'YouTube Music reklam oynatıyor; parçanın başladığı henüz doğrulanmadı.'}
    if state['muted'] or state['volume'] == 0:
        return {'status': 'muted', **result,
                'message': 'Parça ilerliyor ama oynatıcı sessizde veya sesi sıfır.'}
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


YTMUSIC_CONTROL = Tool(
    id='ytmusic_control',
    name='Control YouTube Music playback',
    description='Default tool for music/song playback requests (müzik/şarkı), including seeking the music, even when YouTube Music is not named. '
                'play+query searches and plays; play without query resumes. next/previous skip; seek+seconds jumps to a position. '
                'Omit target to find the open/playing tab automatically; no browser_tabs or status preflight needed. '
                'Results verify the action; do not recheck successful actions. Use status only when asked. '
                'search lists candidates; play_new opens a separate tab only when requested. Omit unused fields.',
    input_model=YTMusicInput,
    handler=ytmusic_control,
    timeout_seconds=55
)
