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

Echo cancellation (Phase 2) plugs in at ``clean_phone_audio``: the single
place where phone audio becomes the audio the PC pipeline hears.
"""
import asyncio
import base64
import json
import logging
import secrets
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


def clean_phone_audio(pcm: bytes, *, user: str, phone: str, turn_id: str) -> bytes:
    """Phase 2 hook: echo-cancel phone PCM against the PC's own playback.

    Called once per phone packet, in order, in the PC-side (local) process,
    just before the packet is handed to the PC assistant. Phase 1 passes the
    audio through unchanged.
    """
    return pcm


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

    def __init__(self, bus, user, send_json, phone_id=None, clock=None):
        self.bus, self.user, self.send_json = bus, user, send_json
        self.id = phone_id or secrets.token_hex(8)
        self.armed = None
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
            if armed:
                await self.bus.publish(_channel(self.user, 'pc'), {'type': 'phone_ready', 'phone': self.id, 'pc': pc})
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
            await self.bus.publish(_channel(self.user, 'pc'), {**start, 'phone': self.id, 'pc': pc})
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
        """A PC decision for this phone's turn."""
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

    def __init__(self, bus, user, send_json, send_bytes, pc_id=None):
        self.bus, self.user = bus, user
        self.send_json, self.send_bytes = send_json, send_bytes
        self.id = pc_id or secrets.token_hex(8)
        self.turn = None  # {'turn_id', 'phone'}

    async def activate(self):
        # The newest PC assistant session takes over; an older one stops receiving.
        await self.bus.set(pc_key(self.user), self.id, PC_TTL_SECONDS)

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
            turn = self.turn
            self.turn = None
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
            self.turn = {'turn_id': message['turn_id'], 'phone': message['phone']}
            await self.send_json({key: value for key, value in message.items() if key not in {'pc', 'phone'}})
        elif kind == 'audio':
            turn = self.turn
            if not turn or message.get('turn_id') != turn['turn_id']:
                return
            try:
                pcm = base64.b64decode(message['pcm'], validate=True)
            except (KeyError, ValueError, TypeError):
                return
            await self.send_bytes(clean_phone_audio(pcm, user=self.user, phone=turn['phone'],
                                                    turn_id=turn['turn_id']))
        elif kind == 'turn_end':
            if self.turn and message.get('turn_id') == self.turn['turn_id']:
                self.turn = None
                await self.send_json({'type': 'turn_end', 'turn_id': message['turn_id'],
                                      'reason': message.get('reason', 'phone')})

    async def close(self):
        if self.turn:
            turn = self.turn
            self.turn = None
            await self.bus.publish(_channel(self.user, 'phone'), {'type': 'turn_end', 'turn_id': turn['turn_id'],
                                                                 'phone': turn['phone']})
        await self.bus.delete_if(pc_key(self.user), self.id)


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
    link = PhoneLink(bus, user, send_json)

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
    link = PcLink(bus, user, send_json, send_bytes)

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
