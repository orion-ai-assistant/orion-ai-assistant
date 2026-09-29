"""Small, verified media operations shared by the two YouTube tools."""
import asyncio
from time import monotonic


def compact_state(state, tab_id):
    result = {'tab_id': tab_id}
    for key in ('title', 'paused', 'current_time', 'duration'):
        value = (state or {}).get(key)
        if value is not None:
            result[key] = round(value, 1) if isinstance(value, float) else value
    for key in ('error', 'ad_showing', 'muted'):
        if (state or {}).get(key):
            result[key] = state[key]
    if (state or {}).get('volume') == 0:
        result['muted'] = True
    return result


async def seek(page, seconds, read_state, *, music=False):
    before = await read_state(page)
    if not before:
        return {'status': 'seek_unavailable', 'tab_id': page.target_id,
                'message': 'Oynatıcı hazır değil.'}
    if music:
        outcome = await page.evaluate('''((seconds) => {
            const v = document.querySelector('video');
            if (!v) return {status:'no_video'};
            if (document.querySelector('.ad-showing, .ytp-ad-player-overlay'))
                return {status:'ad_playing'};
            const player = document.querySelector('#movie_player');
            const timeText = (document.querySelector('ytmusic-player-bar .time-info')?.textContent || '').split('/');
            const parts = (timeText[1] || '').trim().split(':').map(Number);
            const displayedDuration = parts.length >= 2 && parts.length <= 3 && parts.every(Number.isFinite)
                ? parts.reduce((total, part) => total * 60 + part, 0) : null;
            const apiDuration = typeof player?.getDuration === 'function' ? player.getDuration() : null;
            const duration = displayedDuration || apiDuration || v.duration;
            if (!Number.isFinite(duration) || duration <= 0 || seconds >= duration - .25)
                return {status:'seek_unavailable'};
            if (typeof player?.seekTo === 'function') {
                player.seekTo(seconds, true);
                return {status:'seeking', target:seconds, mode:'player_api'};
            }
            const bar = document.querySelector('ytmusic-player-bar #progress-bar');
            if (!bar) return {status:'control_unavailable'};
            const r = bar.getBoundingClientRect();
            if (!r.width || !r.height) return {status:'control_unavailable'};
            const x = r.left + Math.max(1, Math.min(r.width - 1, r.width * seconds / duration));
            const y = r.top + r.height / 2;
            const hit = document.elementFromPoint(x, y);
            if (!(hit === bar || bar.contains(hit))) return {status:'control_unavailable'};
            return {status:'seeking', target:seconds, mode:'player_bar', x, y};
        })(%s)''' % seconds)
        if outcome['status'] == 'seeking' and outcome['mode'] == 'player_bar':
            point = {key: outcome[key] for key in ('x', 'y')}
            for kind in ('mousePressed', 'mouseReleased'):
                await page.send('Input.dispatchMouseEvent',
                                {'type': kind, **point, 'button': 'left', 'clickCount': 1})
    else:
        outcome = await page.evaluate('''((seconds) => {
        const v = document.querySelector('video');
        if (!v) return {status:'no_video'};
        if (document.querySelector('.ad-showing, .ytp-ad-player-overlay'))
            return {status:'ad_playing'};
        if (!Number.isFinite(v.duration) || v.duration <= 0)
            return {status:'seek_unavailable'};
        if (seconds >= v.duration - .25)
            return {status:'seek_unavailable', reason:'requested_position_at_or_past_end'};
        const target = seconds;
        v.currentTime = target;
        return {status:'seeking', target};
    })(%s)''' % seconds, user_gesture=True)
    if outcome['status'] != 'seeking':
        return {**outcome, 'tab_id': page.target_id}
    deadline = monotonic() + 5
    state = None
    while monotonic() < deadline:
        state = await read_state(page)
        if state and _seek_invalid(state, before):
            return {'status': 'seek_unconfirmed', **compact_state(state, page.target_id),
                    'message': 'Konum doğrulanamadı: parça değişti veya oynatma takıldı. Başarı bildirme.'}
        if state and state.get('ready_state', 0) < 2:
            await asyncio.sleep(.1)
            continue
        if state and _near_target(state, outcome['target'], music=music):
            await asyncio.sleep(.5)
            confirmed = await read_state(page)
            if confirmed and not _seek_invalid(confirmed, before) and confirmed.get('ready_state', 0) >= 2:
                if confirmed.get('paused') and _near_target(confirmed, outcome['target'], music=music):
                    return {'status': 'ok', **compact_state(confirmed, page.target_id)}
                if (not confirmed.get('paused') and _near_target(confirmed, outcome['target'], music=music)
                        and confirmed['current_time'] - state['current_time'] >= .2):
                    await asyncio.sleep(.5)
                    later = await read_state(page)
                    if (later and not _seek_invalid(later, before)
                            and later.get('ready_state', 0) >= 2 and not later.get('paused')
                            and _near_target(later, outcome['target'], music=music)
                            and later['current_time'] - confirmed['current_time'] >= .2):
                        return {'status': 'ok', **compact_state(later, page.target_id)}
                    confirmed = later
            return {'status': 'seek_unconfirmed', **compact_state(confirmed, page.target_id),
                    'message': 'Konum doğrulanamadı: oynatma ilerlemiyor. Başarı bildirme.'}
        await asyncio.sleep(.1)
    return {'status': 'seek_unconfirmed', **compact_state(state, page.target_id),
            'message': 'Konum doğrulanamadı: oynatma süre içinde başlamadı. Başarı bildirme.'}


def _near_target(state, target, *, music=False):
    position = state.get('current_time')
    if not (isinstance(position, (int, float)) and target - 1 <= position <= target + 2):
        return False
    if music and state.get('ui_current_time') is not None:
        if not target - 2 <= state['ui_current_time'] <= target + 2:
            return False
    if music and state.get('player_time') is not None:
        if not target - 1 <= state['player_time'] <= target + 2:
            return False
    if music and state.get('player_state') is not None:
        if state['player_state'] not in (1, 2):
            return False
    return True


def _seek_invalid(state, before):
    if state.get('error') or state.get('ad_showing'):
        return True
    if before:
        if any(before.get(key) and state.get(key) and before[key] != state[key]
               for key in ('url', 'title')):
            return True
        if (before.get('duration') and state.get('duration')
                and abs(before['duration'] - state['duration']) > 1):
            return True
    return False


async def skip(page, action, read_state, playback_result, *, music=False):
    before = await read_state(page)
    selector = ('ytmusic-player-bar #next-button, ytmusic-player-bar .next-button'
                if action == 'next' else
                'ytmusic-player-bar #previous-button, ytmusic-player-bar .previous-button') if music else (
                '.ytp-next-button' if action == 'next' else '.ytp-prev-button')
    clicked = await page.evaluate('''(() => {
        const btn = document.querySelector('%s');
        if (!btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true') return false;
        btn.click();
        return true;
    })()''' % selector, user_gesture=True)
    if not clicked:
        return {'status': 'control_unavailable', **compact_state(before, page.target_id)}
    deadline = monotonic() + 5
    state = before
    while monotonic() < deadline:
        await asyncio.sleep(.2)
        state = await read_state(page)
        if not state or state.get('ad_showing'):
            continue
        changed = before and any(state.get(key) and before.get(key) and
                                 state[key] != before[key] for key in ('url', 'title'))
        restarted = (before and action == 'previous' and before.get('current_time', 0) > 3
                     and state.get('current_time', 99) < 2)
        if changed or restarted:
            return playback_result(state, page.target_id)
    return {'status': 'transition_unconfirmed', **compact_state(state, page.target_id),
            'message': 'Track change was not confirmed. Do not repeat automatically or choose another song.'}
