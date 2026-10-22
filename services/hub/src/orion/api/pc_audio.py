"""Optional Windows per-app ducking with a bounded lease; no shell or extra packages."""
import asyncio
import ctypes
import json
import os
import secrets
import uuid
import logging
from pathlib import Path

# Each playing app keeps this fraction of its own level while the user speaks
# (0.15 = 85% quieter). Ducking is per app, never the whole device: loopback is
# captured before device volume and mute, so a muted device leaves echo
# cancellation with a reference whose echo never arrives, and it then
# suppresses the user's voice instead.
DUCK_LEVEL = 0.15
# Short fades instead of jumps. Ducking stays quick so the first words are not
# spoken over full music; restoring is gentler. Per-app levels change before
# the loopback mix, so the echo reference follows every step either way.
DUCK_FADE_SECONDS = 0.15
RESTORE_FADE_SECONDS = 0.4
FADE_STEPS = 5


def duck_store_path():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Orion' / 'pc-audio-duck.json'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'orion' / 'pc-audio-duck.json'


def audio_sessions(levels=None):
    """{instance id: {"app", "level", "muted"}} for every active playback
    session; `levels` ({instance id: level}) is applied first."""
    if os.name != "nt":
        raise RuntimeError("PC ducking requires a Windows Hub")
    from ctypes import wintypes
    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]
        def __init__(self, value):
            super().__init__()
            self.data[:] = uuid.UUID(value).bytes_le
    ole = ctypes.OleDLL("ole32")
    ole.CoInitializeEx(None, 0)
    refs = []
    def call(pointer, slot, argtypes, *args):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[slot])
        result = fn(pointer, *args)
        if result < 0:
            raise OSError(f"Windows audio HRESULT {result:#x}")
    def child(pointer, slot, argtypes, *args):
        out = ctypes.c_void_p()
        call(pointer, slot, [*argtypes, ctypes.POINTER(ctypes.c_void_p)], *args, ctypes.byref(out))
        refs.append(out)
        return out
    def query(pointer, iid):
        return child(pointer, 0, [ctypes.POINTER(GUID)], ctypes.byref(GUID(iid)))
    def text(pointer, slot):
        value = ctypes.c_void_p()
        call(pointer, slot, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(value))
        try:
            return ctypes.wstring_at(value) if value else ""
        finally:
            ole.CoTaskMemFree(value)
    sessions = {}
    try:
        enumerator = ctypes.c_void_p()
        clsid = GUID("bcde0395-e52f-467c-8e3d-c4579291692e")
        iid = GUID("a95664d2-9614-4f35-a746-de8db63617e6")
        ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator))
        refs.append(enumerator)
        # Every active render device: the sound may leave through a Bluetooth
        # speaker or headset rather than the default device.
        devices = child(enumerator, 3, [ctypes.c_int, ctypes.c_uint], 0, 1)
        count = ctypes.c_uint()
        call(devices, 3, [ctypes.POINTER(ctypes.c_uint)], ctypes.byref(count))
        for index in range(count.value):
            device = child(devices, 4, [ctypes.c_uint], index)
            manager = child(device, 3, [ctypes.POINTER(GUID), ctypes.c_uint, ctypes.c_void_p],
                            ctypes.byref(GUID("77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f")), 1, None)
            listing = child(manager, 5, [])
            total = ctypes.c_int()
            call(listing, 3, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(total))
            for number in range(total.value):
                control = child(listing, 4, [ctypes.c_int], number)
                state = ctypes.c_int()
                call(control, 3, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(state))
                if state.value == 2:  # AudioSessionStateExpired
                    continue
                control2 = query(control, "bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
                app, instance = text(control2, 12), text(control2, 13)
                volume = query(control, "87ce5498-68d6-44e5-9215-6da47ef883d8")
                if levels and instance in levels:
                    call(volume, 3, [ctypes.c_float, ctypes.c_void_p], ctypes.c_float(levels[instance]), None)
                level, muted = ctypes.c_float(), wintypes.BOOL()
                call(volume, 4, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(level))
                call(volume, 6, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(muted))
                sessions[instance] = {"app": app, "level": level.value, "muted": bool(muted.value)}
        return sessions
    finally:
        for ref in reversed(refs):
            if ref:
                vtable = ctypes.cast(ref, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(ref)
        ole.CoUninitialize()


class DuckLease:
    def __init__(self, backend=audio_sessions, ttl=90, level=DUCK_LEVEL, poll=0.5, store=None,
                 duck_fade=DUCK_FADE_SECONDS, restore_fade=RESTORE_FADE_SECONDS):
        self.backend, self.ttl, self.level, self.poll = backend, ttl, level, poll
        self.duck_fade, self.restore_fade = duck_fade, restore_fade
        self.store = Path(store) if store else duck_store_path()
        self.lock = asyncio.Lock()
        self.token = self.owner = self.timer = self.watch = None
        # {instance id: {"app", "level" (original), "target" (ducked)}}
        self.ducked = {}

    async def acquire(self, owner):
        async with self.lock:
            if self.token:
                raise ValueError("PC audio is already leased")
            try:
                await self._duck_new()
            except Exception:
                await self._restore()
                raise
            self.owner = owner
            self.token = secrets.token_urlsafe(32)
            self._arm()
            self.watch = asyncio.create_task(self._watch(self.token))
            return self.token

    async def _duck_new(self):
        sessions = await asyncio.to_thread(self.backend)
        fresh = {sid: s for sid, s in sessions.items()
                 if sid not in self.ducked and not s["muted"] and s["level"] > 0}
        if not fresh:
            return
        self.ducked.update({sid: {"app": s["app"], "level": s["level"], "target": s["level"] * self.level}
                            for sid, s in fresh.items()})
        # Saved before writing: a crash mid-duck must still restore on restart.
        self._save()
        await self._fade({sid: (s["level"], self.ducked[sid]["target"]) for sid, s in fresh.items()},
                         self.duck_fade)

    async def _fade(self, levels, seconds):
        """{instance id: (from, to)}; the last step writes exactly `to`."""
        steps = FADE_STEPS if seconds > 0 else 1
        for step in range(1, steps + 1):
            if step > 1:
                await asyncio.sleep(seconds / steps)
            share = step / steps
            await asyncio.to_thread(self.backend, {sid: a + (b - a) * share for sid, (a, b) in levels.items()})

    async def _watch(self, token):
        # Apps that start playing during the lease are ducked too.
        while True:
            await asyncio.sleep(self.poll)
            async with self.lock:
                if self.token != token:
                    return
                try:
                    await self._duck_new()
                except Exception:
                    logging.exception("PC ducking of a new app failed")

    async def _restore(self):
        if not self.ducked:
            return
        sessions = await asyncio.to_thread(self.backend)
        levels = {}
        for sid, saved in self.ducked.items():
            # An app restarted after a Hub crash keeps its level under a new instance.
            matches = [sid] if sid in sessions else [i for i, s in sessions.items() if s["app"] == saved["app"]]
            for match in matches:
                # A level the user changed while ducked is theirs; only undo our own.
                if abs(sessions[match]["level"] - saved["target"]) < .01:
                    levels[match] = (sessions[match]["level"], saved["level"])
        if levels:
            await self._fade(levels, self.restore_fade)
        self.ducked = {}
        self._save()

    def _save(self):
        if self.ducked:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(json.dumps(self.ducked), encoding="utf-8")
        else:
            self.store.unlink(missing_ok=True)

    async def recover(self):
        """Restore levels a crashed Hub left ducked."""
        async with self.lock:
            if self.token or not self.store.exists():
                return
            try:
                self.ducked = json.loads(self.store.read_text(encoding="utf-8"))
            except ValueError:
                self.store.unlink(missing_ok=True)
                return
            await self._restore()

    def _arm(self):
        if self.timer:
            self.timer.cancel()
        token = self.token
        async def expire():
            await asyncio.sleep(self.ttl)
            try:
                await self.release(self.owner, token)
            except Exception:
                logging.exception("PC audio restore failed; retrying after lease timeout")
                self._arm()
        self.timer = asyncio.create_task(expire())

    async def renew(self, owner, token):
        async with self.lock:
            if (owner, token) != (self.owner, self.token) or not token:
                raise ValueError("Unknown mute lease")
            self._arm()

    async def release(self, owner, token):
        async with self.lock:
            if (owner, token) != (self.owner, self.token) or not token:
                return False
            if self.watch:
                self.watch.cancel()
            await self._restore()
            if self.timer and self.timer is not asyncio.current_task():
                self.timer.cancel()
            self.token = self.owner = self.timer = self.watch = None
            return True

    async def close(self):
        if self.token:
            await self.release(self.owner, self.token)

lease = DuckLease()
