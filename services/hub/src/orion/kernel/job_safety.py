"""Conservative crash recovery for a single native Worker (Redis 5 compatible).

The journal survives Worker restarts; Redis persistence/eviction remains an
installation responsibility. Started work is never automatically replayed.
"""
import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path
from uuid import uuid4

import psutil
from redis.exceptions import RedisError

from orion.contracts.constants import ACTIVE_TURN_KEY_PREFIX, CHAT_STATE_KEY_PREFIX
from orion.contracts.events import StreamEvent

OWNER_KEY = "orion:worker:owner"
JOB_PREFIX = "orion:job:"
WAITING_KEY = "orion:jobs:unfinished"
QUEUE_TIMEOUT_SECONDS = 60
TERMINAL_TTL = 7 * 86400
INIT_JOURNAL = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'phase', 'queued', 'turn_id', ARGV[1], 'chat_id', ARGV[2], 'fields', ARGV[3])
return 1
"""
INTERRUPTED = ("Worker işlemi tamamlamadan kapandı. Önceki araç işleminin sonucu belirsiz olabilir; "
               "otomatik tekrar yapılmadı. Sonucu kontrol edip mesajı yeniden gönderebilirsiniz.")
QUEUE_TIMEOUT = "Mesaj kuyrukta beklerken zaman aşımına uğradı. Worker çalışmıyor veya yoğun olabilir. Mesajı yeniden gönderebilirsiniz."


def job_key(turn_id):
    return JOB_PREFIX + turn_id


def owner_alive(raw):
    """False only for a provably dead local process; None means unknown."""
    if not raw:
        return None
    try:
        owner = json.loads(raw)
        if owner['host'] != socket.gethostname():
            return None
        proc = psutil.Process(owner['pid'])
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE and proc.create_time() == owner['created']
    except psutil.NoSuchProcess:
        return False
    except (ValueError, KeyError, TypeError, psutil.AccessDenied):
        return None


class SingleWorkerLock:
    """An OS lock has no expiry and is released by the OS on process death."""

    def __init__(self, path=None):
        from .data_paths import hub_data
        self.path = Path(path) if path else hub_data() / 'worker.lock'
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError('Another Orion Worker is already running') from exc
        if os.fstat(self.file.fileno()).st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        return self

    def __exit__(self, *args):
        if os.name == 'nt':
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()


COMPARE_SET = """
if (redis.call('GET', KEYS[1]) or '') ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[2]); return 1
"""
COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


async def acquire_owner(redis):
    # Also refuse a still-running pre-upgrade Worker that has no lock/journal.
    process = psutil.Process()
    # Windows venv redirectors can retain the same command line as their child.
    # They are this Worker's launch chain, not a second Worker.
    launch_chain = {os.getpid(), *(parent.pid for parent in process.parents())}
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            if proc.pid not in launch_chain and 'orion.worker.main' in (proc.info['cmdline'] or []):
                # Separate installations have independent Redis journals/locks.
                # Legacy -m launchers identify their installation by cwd; new
                # launchers also carry an absolute process_child.py path.
                root = Path(__file__).resolve().parents[5]
                command = proc.info['cmdline'] or []
                adapters = [Path(arg) for arg in command if Path(arg).name == 'process_child.py']
                same_root = (any(path.resolve() == root / 'services/shared/process_child.py' for path in adapters)
                             if adapters else Path(proc.cwd()).resolve() in {root, root / 'services/hub'})
                if same_root:
                    raise RuntimeError('Another local Orion Worker is running (possibly pre-upgrade)')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    current = await redis.get(OWNER_KEY)
    if current and owner_alive(current) is not False:
        raise RuntimeError('Worker ownership is active or cannot be verified; refusing recovery')
    owner = json.dumps({'host': socket.gethostname(), 'pid': os.getpid(),
                        'created': process.create_time(), 'token': str(uuid4())})
    if not await redis.eval(COMPARE_SET, 1, OWNER_KEY, current or '', owner):
        raise RuntimeError('Another Worker acquired ownership')
    return owner


START = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return redis.error_reply('Worker ownership lost') end
if redis.call('HGET', KEYS[2], 'phase') ~= 'queued' then return 0 end
if redis.call('HGET', KEYS[3], 'active_turn_id') ~= ARGV[2]
  or redis.call('HGET', KEYS[3], 'status') ~= 'queued'
  or redis.call('GET', KEYS[4]) ~= ARGV[2] then return 0 end
redis.call('HSET', KEYS[2], 'phase', 'started', 'owner', ARGV[1], 'started_at', ARGV[3])
redis.call('HSET', KEYS[3], 'status', 'processing', 'updated_at', ARGV[3])
redis.call('EXPIRE', KEYS[3], ARGV[4])
return 1
"""


async def mark_started(redis, fields, owner):
    turn = fields['turn_id']
    return bool(await redis.eval(START, 4, OWNER_KEY, job_key(turn),
                                CHAT_STATE_KEY_PREFIX + fields['chat_id'],
                                ACTIVE_TURN_KEY_PREFIX + fields['chat_id'], owner, turn, utc_now(), TERMINAL_TTL))


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


FAIL = """
local phase = redis.call('HGET', KEYS[1], 'phase')
if phase == 'terminal' then return 0 end
if ARGV[5] ~= '' and phase ~= ARGV[5] then return 0 end
redis.call('HSET', KEYS[1], 'phase', 'terminal', 'status', 'failed', 'error', ARGV[2],
  'chat_id', ARGV[4], 'turn_id', ARGV[1], 'updated_at', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[6])
redis.call('ZREM', KEYS[4], ARGV[1])
if redis.call('HGET', KEYS[2], 'active_turn_id') ~= ARGV[1] then return 1 end
local status = redis.call('HGET', KEYS[2], 'status')
if status == 'completed' or status == 'stopped' or status == 'failed' then
  redis.call('HSET', KEYS[1], 'status', status, 'error', redis.call('HGET', KEYS[2], 'error') or '')
  return 0
end
redis.call('HSET', KEYS[2], 'status', 'failed', 'error', ARGV[2], 'result_json', '{}', 'updated_at', ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[6])
if redis.call('GET', KEYS[3]) == ARGV[1] then redis.call('DEL', KEYS[3]) end
return 2
"""


async def fail_job(redis, fields, message=INTERRUPTED, expected_phase=''):
    turn, chat = fields['turn_id'], fields['chat_id']
    state = await redis.hgetall(CHAT_STATE_KEY_PREFIX + chat)
    changed = await redis.eval(FAIL, 5, job_key(turn), CHAT_STATE_KEY_PREFIX + chat,
                               ACTIVE_TURN_KEY_PREFIX + chat, WAITING_KEY, 'chat:history:' + chat,
                               turn, message, utc_now(), chat, expected_phase, TERMINAL_TTL,
                               json.dumps({'role': 'assistant', 'content': message, 'turn_id': turn,
                                           'failed_turn': True}))
    if changed == 2:
        from orion.kernel.chat_history import persist_failed_turn
        user_message = json.loads(state['current_user_message']) if state.get('current_user_message') else {
            'role': 'user', 'content': state.get('current_prompt', '')}
        await persist_failed_turn(redis, chat, turn, user_message, message, state)
        # The durable status is authoritative even when publication fails.
        try:
            await redis.publish(fields['channel'], StreamEvent.error(chat, message, turn_id=turn).model_dump_json())
        except (RedisError, OSError):
            logging.warning('Terminal event publication failed for turn %s', turn)
    return changed


async def sweep_waiting(redis):
    """API-owned watchdog; queued work expires even when no Worker is running."""
    async for turn, deadline in redis.zscan_iter(WAITING_KEY, count=100):
        job = await redis.hgetall(job_key(turn))
        if not job:
            # An expired/evicted journal never authorizes replay.
            await redis.zrem(WAITING_KEY, turn)
            continue
        fields = json.loads(job['fields'])
        if job['phase'] in ('queued', 'legacy') and float(deadline) <= time.time():
            await fail_job(redis, fields, QUEUE_TIMEOUT, expected_phase=job['phase'])
        elif job['phase'] == 'started' and owner_alive(job.get('owner')) is False:
            await fail_job(redis, fields, INTERRUPTED, expected_phase='started')
        elif job['phase'] == 'terminal':
            # A client may retry the admission transaction after a lost reply.
            # The immutable journal prevents replay and repairs a stale chat
            # snapshot before removing its watchdog entry.
            await redis.eval(RESTORE_TERMINAL, 4, job_key(turn),
                CHAT_STATE_KEY_PREFIX + fields['chat_id'], ACTIVE_TURN_KEY_PREFIX + fields['chat_id'],
                WAITING_KEY, turn, TERMINAL_TTL)
            await redis.zrem(WAITING_KEY, turn)


async def queue_watchdog(redis):
    # Pre-upgrade queued messages have no replay authorization. Index them so
    # they still receive a visible timeout if the Worker never returns.
    initialized = False
    while True:
        try:
            if not initialized:
                await index_legacy_queued(redis)
                initialized = True
            await sweep_waiting(redis)
        except (RedisError, OSError):
            logging.warning('Queue watchdog Redis unavailable; retrying', exc_info=True)
        # A Redis await can complete while cancellation is being delivered and
        # consume CancelledError internally. Do not resume the perpetual loop
        # with a pending shutdown request; API lifespan is waiting for this task.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        await asyncio.sleep(5)


RESTORE_TERMINAL = """
if redis.call('HGET', KEYS[1], 'phase') ~= 'terminal' then return 0 end
if redis.call('HGET', KEYS[2], 'active_turn_id') == ARGV[1] then
  local state = redis.call('HGET', KEYS[2], 'status')
  if state == 'queued' or state == 'processing' then
    redis.call('HSET', KEYS[2], 'status', redis.call('HGET', KEYS[1], 'status'),
      'error', redis.call('HGET', KEYS[1], 'error') or '',
      'result_json', redis.call('HGET', KEYS[1], 'result_json') or '{}',
      'updated_at', redis.call('HGET', KEYS[1], 'updated_at') or '')
    redis.call('EXPIRE', KEYS[2], ARGV[2])
    if redis.call('GET', KEYS[3]) == ARGV[1] then redis.call('DEL', KEYS[3]) end
  end
end
redis.call('ZREM', KEYS[4], ARGV[1]); return 1
"""


async def index_legacy_queued(redis):
    from datetime import datetime
    script = """
    if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
    if redis.call('HGET', KEYS[2], 'status') ~= 'queued'
      or redis.call('HGET', KEYS[2], 'active_turn_id') ~= ARGV[1] then return 0 end
    redis.call('HSET', KEYS[1], 'phase', 'legacy', 'fields', ARGV[2])
    redis.call('ZADD', KEYS[3], ARGV[3], ARGV[1]); return 1
    """
    async for key in redis.scan_iter(CHAT_STATE_KEY_PREFIX + '*', count=100):
        state = await redis.hgetall(key)
        if state.get('status') != 'queued' or not state.get('active_turn_id'):
            continue
        fields = {'turn_id': state['active_turn_id'], 'chat_id': state['chat_id'],
                  'channel': state['channel']}
        deadline = datetime.fromisoformat(state['created_at']).timestamp() + QUEUE_TIMEOUT_SECONDS
        await redis.eval(script, 3, job_key(fields['turn_id']), key, WAITING_KEY,
                         fields['turn_id'], json.dumps(fields), deadline)
