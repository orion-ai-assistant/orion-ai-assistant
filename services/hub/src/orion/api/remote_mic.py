"""Phone as the PC assistant's microphone ("PC mikrofonu").

The phone runs only wake detection. On "Orion" it streams its uncut wake
history (pre-roll) and then live PCM16 mono 16 kHz. The Hub relays the turn to
the account's active PC assistant session; the PC decides when the turn ends.

The mobile listener and the PC (local) listener are separate processes, so the
two endpoints meet on Redis pub/sub. PCM crosses Redis as base64 and reaches
the PC web client as binary frames, in order, after its ``turn_start``.

Protocol (JSON text frames, PCM as binary frames):

  phone -> Hub : turn_start {turn_id, sample_rate, start_sample, wake_end_sample,
                 wake_score, preroll_samples}, <binary PCM>, turn_end {turn_id,
                 reason}, heartbeat
  Hub -> phone : arm, disarm, turn_reject {turn_id, reason}, turn_end {turn_id},
                 heartbeat
  Hub -> PC    : phone_ready, turn_start (as sent), <binary PCM>, turn_end
                 {turn_id, reason}, heartbeat
  PC -> Hub    : turn_end {turn_id}, turn_reject {turn_id, reason}, heartbeat

Echo cancellation (Phase 2): ``PhoneAudioCleaner`` is the single place where
phone audio becomes the audio the PC pipeline hears. It runs in the PC-side
(local) process, which also captures the loudspeaker loopback, and uses
``orion.audio.echo_cancel.EchoCanceller`` when that module is available.
Without it, or after any canceller error, the audio passes through unchanged.
"""
import asyncio
import base64
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager, suppress

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

log = logging.getLogger(__name__)
router = APIRouter()

SAMPLE_RATE = 16000
# The phone keeps about two seconds of wake history; allow some margin.
MAX_PREROLL_SAMPLES = SAMPLE_RATE * 4
MAX_FRAME_BYTES = 64000  # Two seconds; the phone sends 100 ms packets.
PC_TTL_SECONDS = 15  # The PC sends a heartbeat every 5 s.
TURN_TTL_SECONDS = 30  # Refreshed by phone audio and heartbeats.
HEARTBEAT_SECONDS = 5
TURN_ID_LIMIT = 64


class PhoneAudioCleaner:
    """Echo-cancels phone PCM against this PC's playback, sample-aligned.

    Wraps the CPU-bound ``EchoCanceller`` (calls run in a worker thread, in
    order per phone). Output keeps the input's timeline, so the PC still finds
    the pre-roll's end and ``wake_end_sample`` by counting samples; chunks may
    be shorter while the reference catches up. ``canceller=None`` passes audio
    through unchanged.
    """

    def __init__(self, canceller=None):
        self.canceller = canceller
        self._armed = 0
        self._raw = set()  # devices whose current turn fell back to raw audio

    async def _call(self, method, *args):
        return await asyncio.to_thread(getattr(self.canceller, method), *args)

    async def arm(self):
        self._armed += 1
        if self.canceller is not None and self._armed == 1:
            try:
                await self._call('arm')
            except Exception:
                log.warning('Echo canceller could not arm; phone audio passes through', exc_info=True)

    async def disarm(self):
        self._armed = max(0, self._armed - 1)
        if self.canceller is not None and self._armed == 0:
            with suppress(Exception):
                await self._call('disarm')

    async def start_turn(self, device, preroll, start_time):
        self._raw.discard(device)
        if self.canceller is None:
            return preroll
        try:
            return await self._call('start_turn', device, preroll, start_time)
        except Exception:
            log.warning('Echo canceller failed at turn start; raw audio for this turn', exc_info=True)
            self._raw.add(device)
            return preroll

    async def process(self, device, pcm):
        if self.canceller is None or device in self._raw:
            return pcm
        try:
            return await self._call('process', device, pcm)
        except Exception:
            # A switch to raw mid-turn may shift the timeline by the engine
            # latency; still better than silence.
            log.warning('Echo canceller failed mid-turn; raw audio for this turn', exc_info=True)
            self._raw.add(device)
            return pcm

    async def end_turn(self, device):
        """Close the turn. The tail is not needed: the PC already ended or left it."""
        self._raw.discard(device)
        if self.canceller is None:
            return
        with suppress(Exception):
            await self._call('end_turn', device)


_shared_cleaner = None


def shared_cleaner():
    """One canceller per PC-side process (one loopback capture, warm per-phone state)."""
    global _shared_cleaner
    if _shared_cleaner is None:
        canceller = None
        try:
            from orion.audio.echo_cancel import EchoCanceller
            canceller = EchoCanceller()
        except Exception:
            log.info('Echo cancellation module unavailable; phone audio passes through', exc_info=True)
        _shared_cleaner = PhoneAudioCleaner(canceller)
    return _shared_cleaner


def _channel(user, side):
    return f'orion:remote-mic:{user}:{side}'


def pc_key(user):
    return f'orion:remote-mic:{user}:pc'


def turn_key(user):
    return f'orion:remote-mic:{user}:turn'


def _valid_turn_id(value):
    return isinstance(value, str) and 0 < len(value) <= TURN_ID_LIMIT and value.isprintable()


def _int(value, minimum, maximum):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def validate_turn_start(message):
    """Return a normalized turn_start, or raise ValueError."""
    turn_id = message.get('turn_id')
    if not _valid_turn_id(turn_id):
        raise ValueError('turn_id')
    if message.get('sample_rate') != SAMPLE_RATE:
        raise ValueError('sample_rate')
    start = message.get('start_sample')
    wake_end = message.get('wake_end_sample')
    preroll = message.get('preroll_samples')
    if not _int(start, 0, 2 ** 53) or not _int(preroll, 0, MAX_PREROLL_SAMPLES):
        raise ValueError('start_sample/preroll_samples')
    # The wake end lies inside the pre-roll on the phone's own sample clock.
    if not _int(wake_end, start, start + preroll):
        raise ValueError('wake_end_sample')
    score = message.get('wake_score')
    if score is not None and not (isinstance(score, (int, float)) and 0 <= score <= 1):
        raise ValueError('wake_score')
    return {'type': 'turn_start', 'turn_id': turn_id, 'sample_rate': SAMPLE_RATE,
            'start_sample': start, 'wake_end_sample': wake_end,
            'wake_score': None if score is None else float(score), 'preroll_samples': preroll}


class RedisBus:
    """Pub/sub plus the two shared keys, on the Hub's Redis (decoded strings)."""

    COMPARE_DELETE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
    COMPARE_EXPIRE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) end return 0"

    def __init__(self, redis):
        self.redis = redis

    async def publish(self, channel, message):
        await self.redis.publish(channel, json.dumps(message))

    @asynccontextmanager
    async def subscribe(self, channel):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)

        async def messages():
            while True:
                item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if item and item.get('type') == 'message':
                    with suppress(ValueError, TypeError):
                        yield json.loads(item['data'])
        try:
            yield messages()
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(channel)
                await pubsub.close()

    async def get(self, key):
        return await self.redis.get(key)

    async def set(self, key, value, ttl, only_new=False):
        return bool(await self.redis.set(key, value, ex=ttl, nx=only_new))

    async def refresh(self, key, value, ttl):
        return bool(await self.redis.eval(self.COMPARE_EXPIRE, 1, key, value, ttl))

    async def delete_if(self, key, value):
        return bool(await self.redis.eval(self.COMPARE_DELETE, 1, key, value))


class PhoneLink:
    """One phone connection in "PC mikrofonu" mode (mobile listener process)."""

    def __init__(self, bus, user, send_json, phone_id=None, clock=None, device=None):
        self.bus, self.user, self.send_json = bus, user, send_json
        self.id = phone_id or secrets.token_hex(8)
        # Stable across reconnects, so the echo canceller stays warm per phone.
        self.device = device or self.id
        self.armed = None
        self.ready_for = None  # the PC session last told this phone is ready
        self.turn = None  # {'turn_id', 'pc'}
        self.preroll_left = 0
        self._clock = clock or asyncio.get_running_loop().time

    @property
    def owner(self):
        return f'{self.id}:{self.turn["turn_id"]}' if self.turn else None

    async def sync_arm(self):
        """Arm while an active PC assistant exists. Ends a turn whose PC left."""
        pc = await self.bus.get(pc_key(self.user))
        if self.turn and pc != self.turn['pc']:
            await self._end('pc_gone', notify_phone=True, notify_pc=False)
        armed = pc is not None
        if armed != self.armed:
            self.armed = armed
            await self.send_json({'type': 'arm' if armed else 'disarm'})
        # Every new PC session hears it once, also when the PC restarted its
        # listening between two polls and the phone never saw it disarmed.
        if armed and pc != self.ready_for:
            await self.bus.publish(_channel(self.user, 'pc'), {'type': 'phone_ready', 'phone': self.id, 'pc': pc})
        self.ready_for = pc
        if self.turn:
            await self.bus.refresh(turn_key(self.user), self.owner, TURN_TTL_SECONDS)
        return armed

    async def on_text(self, message):
        kind = message.get('type')
        if kind == 'heartbeat':
            return
        if kind == 'turn_start':
            try:
                start = validate_turn_start(message)
            except ValueError as error:
                return await self.send_json({'type': 'turn_reject', 'turn_id': message.get('turn_id'),
                                             'reason': f'invalid:{error}'})
            if self.turn:
                await self._end('replaced', notify_phone=False, notify_pc=True)
            pc = await self.bus.get(pc_key(self.user))
            if pc is None:
                return await self.send_json({'type': 'turn_reject', 'turn_id': start['turn_id'], 'reason': 'no_pc'})
            owner = f'{self.id}:{start["turn_id"]}'
            if not await self.bus.set(turn_key(self.user), owner, TURN_TTL_SECONDS, only_new=True):
                return await self.send_json({'type': 'turn_reject', 'turn_id': start['turn_id'], 'reason': 'busy'})
            self.turn = {'turn_id': start['turn_id'], 'pc': pc}
            self.preroll_left = start['preroll_samples'] * 2
            await self.bus.publish(_channel(self.user, 'pc'), {**start, 'phone': self.id, 'pc': pc,
                                                              'device': self.device})
            return
        if kind == 'turn_end':
            if self.turn and message.get('turn_id') == self.turn['turn_id']:
                reason = message.get('reason') if isinstance(message.get('reason'), str) else 'phone'
                await self._end(reason[:40], notify_phone=False, notify_pc=True)

    async def on_audio(self, pcm):
        """Relay one PCM packet of the current turn; drop audio outside a turn."""
        if not self.turn or not pcm or len(pcm) % 2 or len(pcm) > MAX_FRAME_BYTES:
            return False
        self.preroll_left = max(0, self.preroll_left - len(pcm))
        await self.bus.publish(_channel(self.user, 'pc'), {
            'type': 'audio', 'turn_id': self.turn['turn_id'], 'phone': self.id, 'pc': self.turn['pc'],
            'pcm': base64.b64encode(pcm).decode('ascii')})
        return True

    async def on_bus(self, message):
        """A PC decision for this phone's turn, or a new PC session starting."""
        if message.get('type') == 'pc_active':
            await self.sync_arm()
            return
        if not self.turn or message.get('turn_id') != self.turn['turn_id'] or message.get('phone') != self.id:
            return
        kind = message.get('type')
        if kind == 'turn_end':
            await self._end('pc', notify_phone=True, notify_pc=False)
        elif kind == 'turn_reject':
            reason = message.get('reason') if isinstance(message.get('reason'), str) else 'rejected'
            await self._end(reason[:40], notify_phone=True, notify_pc=False, reject=True)

    async def _end(self, reason, *, notify_phone, notify_pc, reject=False):
        turn, owner = self.turn, self.owner
        self.turn = None
        self.preroll_left = 0
        if not turn:
            return
        await self.bus.delete_if(turn_key(self.user), owner)
        if notify_pc:
            await self.bus.publish(_channel(self.user, 'pc'), {'type': 'turn_end', 'turn_id': turn['turn_id'],
                                                              'phone': self.id, 'pc': turn['pc'], 'reason': reason})
        if notify_phone:
            await self.send_json({'type': 'turn_reject' if reject else 'turn_end',
                                  'turn_id': turn['turn_id'], 'reason': reason})

    async def close(self):
        await self._end('phone_disconnected', notify_phone=False, notify_pc=True)


class PcLink:
    """The PC assistant session that receives phone turns (local listener process)."""

    def __init__(self, bus, user, send_json, send_bytes, pc_id=None, cleaner=None, clock=time.perf_counter):
        self.bus, self.user = bus, user
        self.send_json, self.send_bytes = send_json, send_bytes
        self.id = pc_id or secrets.token_hex(8)
        self.cleaner = cleaner or PhoneAudioCleaner()
        self.clock = clock
        self.turn = None  # {'turn_id', 'phone', 'device', 'preroll', 'need', 'started', 'open'}
        self._armed = False

    async def activate(self):
        # The newest PC assistant session takes over; an older one stops receiving.
        await self.bus.set(pc_key(self.user), self.id, PC_TTL_SECONDS)
        if not self._armed:
            self._armed = True
            await self.cleaner.arm()  # Start the loudspeaker reference ring.

    async def announce(self):
        """Ask connected phones to report now instead of at their next poll."""
        await self.bus.publish(_channel(self.user, 'phone'), {'type': 'pc_active', 'pc': self.id})

    async def _close_turn(self):
        turn, self.turn = self.turn, None
        if turn and turn['open']:
            await self.cleaner.end_turn(turn['device'])
        return turn

    async def _deliver(self, pcm):
        """Phone PCM in arrival order -> cleaned PCM to the PC assistant."""
        turn = self.turn
        if not turn['open']:
            take = turn['need'] - len(turn['preroll'])
            turn['preroll'] += pcm[:take]
            pcm = pcm[take:]
            if len(turn['preroll']) < turn['need']:
                return
            turn['open'] = True
            out = await self.cleaner.start_turn(turn['device'], bytes(turn['preroll']), turn['started'])
            if out and self.turn is turn:
                await self.send_bytes(out)
            if not pcm:
                return
        out = await self.cleaner.process(turn['device'], pcm)
        if out and self.turn is turn:
            await self.send_bytes(out)

    async def heartbeat(self):
        """Keep this PC active. False once a newer PC session took over."""
        return await self.bus.refresh(pc_key(self.user), self.id, PC_TTL_SECONDS)

    async def on_text(self, message):
        kind = message.get('type')
        if kind == 'heartbeat':
            if not await self.heartbeat():
                await self.send_json({'type': 'inactive'})
            return
        if kind in {'turn_end', 'turn_reject'} and self.turn and message.get('turn_id') == self.turn['turn_id']:
            turn = await self._close_turn()
            reply = {'type': kind, 'turn_id': turn['turn_id'], 'phone': turn['phone']}
            if kind == 'turn_reject':
                reason = message.get('reason')
                reply['reason'] = reason[:40] if isinstance(reason, str) else 'rejected'
            await self.bus.publish(_channel(self.user, 'phone'), reply)

    async def on_bus(self, message):
        kind = message.get('type')
        if message.get('pc') != self.id:
            return
        if kind == 'phone_ready':
            await self.send_json({'type': 'phone_ready'})
        elif kind == 'turn_start':
            await self._close_turn()
            # The first live sample follows the pre-roll; it reaches the Hub now.
            self.turn = {'turn_id': message['turn_id'], 'phone': message['phone'],
                         'device': message.get('device') or message['phone'],
                         'preroll': bytearray(), 'need': message['preroll_samples'] * 2,
                         'started': self.clock(), 'open': False}
            await self.send_json({key: value for key, value in message.items()
                                  if key not in {'pc', 'phone', 'device'}})
        elif kind == 'audio':
            turn = self.turn
            if not turn or message.get('turn_id') != turn['turn_id']:
                return
            try:
                pcm = base64.b64decode(message['pcm'], validate=True)
            except (KeyError, ValueError, TypeError):
                return
            if pcm:
                await self._deliver(pcm)
        elif kind == 'turn_end':
            if self.turn and message.get('turn_id') == self.turn['turn_id']:
                await self._close_turn()
                await self.send_json({'type': 'turn_end', 'turn_id': message['turn_id'],
                                      'reason': message.get('reason', 'phone')})

    async def close(self):
        turn = await self._close_turn()
        if turn:
            await self.bus.publish(_channel(self.user, 'phone'), {'type': 'turn_end', 'turn_id': turn['turn_id'],
                                                                 'phone': turn['phone']})
        await self.bus.delete_if(pc_key(self.user), self.id)
        if self._armed:
            self._armed = False
            await self.cleaner.disarm()


async def _authenticate(websocket):
    from orion.api.auth_routes import authenticate_token
    token = websocket.query_params.get('token') or websocket.headers.get('authorization', '').removeprefix('Bearer ')
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None
    try:
        return await authenticate_token(token, websocket)
    except HTTPException as error:
        await websocket.close(code=1013 if error.status_code == 503 else status.WS_1008_POLICY_VIOLATION)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    return None


async def _run(tasks):
    tasks = [asyncio.create_task(task) for task in tasks]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception() and not isinstance(task.exception(), WebSocketDisconnect):
                log.warning('Remote microphone relay stopped: %r', task.exception())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _receive(websocket, on_text, on_bytes=None):
    while True:
        message = await websocket.receive()
        if message['type'] == 'websocket.disconnect':
            return
        if message.get('bytes') is not None:
            if on_bytes:
                await on_bytes(message['bytes'])
        elif message.get('text') is not None:
            try:
                data = json.loads(message['text'])
            except ValueError:
                continue
            if isinstance(data, dict):
                await on_text(data)


@router.websocket('/api/v1/audio/remote-mic/phone')
async def phone_endpoint(websocket: WebSocket):
    if not getattr(websocket.app.state, 'mobile', False):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user = await _authenticate(websocket)
    if user is None:
        return
    await websocket.accept()
    bus = RedisBus(websocket.app.state.redis)
    lock = asyncio.Lock()

    async def send_json(message):
        async with lock:
            await websocket.send_text(json.dumps(message))
    device = getattr(websocket.state, 'device_id', None)
    link = PhoneLink(bus, user, send_json, device=str(device) if device else None)

    async def watch():
        # Heartbeats always flow, so the phone also detects a dead connection
        # while disarmed. During a turn the PC's presence is checked faster.
        while True:
            await link.sync_arm()
            await send_json({'type': 'heartbeat'})
            await asyncio.sleep(HEARTBEAT_SECONDS if not link.turn else 2)

    async def listen():
        async with bus.subscribe(_channel(user, 'phone')) as messages:
            async for message in messages:
                await link.on_bus(message)
    try:
        await _run([watch(), listen(), _receive(websocket, link.on_text, link.on_audio)])
    finally:
        with suppress(Exception):
            await link.close()
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()


@router.websocket('/api/v1/audio/remote-mic/pc')
async def pc_endpoint(websocket: WebSocket):
    from orion.api.access_boundary import is_desktop_peer
    # The reply plays on this PC and Phase 2 cancels this PC's own playback.
    if getattr(websocket.app.state, 'mobile', False) or not is_desktop_peer(websocket.scope):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user = await _authenticate(websocket)
    if user is None:
        return
    await websocket.accept()
    bus = RedisBus(websocket.app.state.redis)
    lock = asyncio.Lock()

    async def send_json(message):
        async with lock:
            await websocket.send_text(json.dumps(message))

    async def send_bytes(data):
        async with lock:
            await websocket.send_bytes(data)
    link = PcLink(bus, user, send_json, send_bytes, cleaner=shared_cleaner())

    async def listen(ready):
        async with bus.subscribe(_channel(user, 'pc')) as messages:
            ready.set()
            async for message in messages:
                await link.on_bus(message)

    async def start():
        ready = asyncio.Event()
        listener = asyncio.create_task(listen(ready))
        try:
            await ready.wait()
            # Subscribe before arming so the first turn cannot be missed.
            await link.activate()
            await send_json({'type': 'ready', 'pc': link.id})
            # After 'ready', so the PC never sees phone_ready before it.
            await link.announce()
            await listener
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
    try:
        await _run([start(), _receive(websocket, link.on_text)])
    finally:
        with suppress(Exception):
            await link.close()
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close()
