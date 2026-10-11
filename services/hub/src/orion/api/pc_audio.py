"""Optional Windows endpoint mute with a bounded lease; no shell or extra packages."""
import asyncio
import ctypes
import os
import secrets
import uuid
import logging


def endpoint_mute(device_id=None, muted=None):
    if os.name != "nt":
        raise RuntimeError("PC mute requires a Windows Hub")
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
    try:
        enumerator = ctypes.c_void_p()
        clsid = GUID("bcde0395-e52f-467c-8e3d-c4579291692e")
        iid = GUID("a95664d2-9614-4f35-a746-de8db63617e6")
        ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator))
        refs.append(enumerator)
        device = ctypes.c_void_p()
        if device_id is None:
            call(enumerator, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], 0, 1, ctypes.byref(device))
        else:
            call(enumerator, 5, [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)], device_id, ctypes.byref(device))
        refs.append(device)
        name = ctypes.c_void_p()
        call(device, 5, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(name))
        try:
            name_value = ctypes.wstring_at(name)
        finally:
            ole.CoTaskMemFree(name)
        volume = ctypes.c_void_p()
        iid_volume = GUID("5cdf2c82-841e-4546-9722-0cf74078229a")
        call(device, 3, [ctypes.POINTER(GUID), ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(iid_volume), 1, None, ctypes.byref(volume))
        refs.append(volume)
        if muted is not None:
            call(volume, 14, [wintypes.BOOL, ctypes.c_void_p], bool(muted), None)
        state = wintypes.BOOL()
        call(volume, 15, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(state))
        return {"device": name_value, "muted": bool(state.value)}
    finally:
        for ref in reversed(refs):
            vtable = ctypes.cast(ref, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(ref)
        ole.CoUninitialize()


class MuteLease:
    def __init__(self, backend=endpoint_mute, ttl=90):
        self.backend, self.ttl = backend, ttl
        self.lock = asyncio.Lock()
        self.token = self.owner = self.snapshot = self.timer = None

    async def acquire(self, owner):
        async with self.lock:
            if self.token:
                raise ValueError("PC audio is already leased")
            snapshot = await asyncio.to_thread(self.backend)
            try:
                await asyncio.to_thread(self.backend, snapshot["device"], True)
            except Exception:
                await asyncio.to_thread(self.backend, snapshot["device"], snapshot["muted"])
                raise
            self.snapshot, self.owner = snapshot, owner
            self.token = secrets.token_urlsafe(32)
            self._arm()
            return self.token

    def _arm(self):
        if self.timer:
            self.timer.cancel()
        token = self.token
        async def expire():
            await asyncio.sleep(self.ttl)
            try:
                await self.release(self.owner, token)
            except Exception:
                logging.exception("PC mute restore failed; retrying after lease timeout")
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
            snapshot = self.snapshot
            await asyncio.to_thread(self.backend, snapshot["device"], snapshot["muted"])
            if self.timer and self.timer is not asyncio.current_task():
                self.timer.cancel()
            self.token = self.owner = self.snapshot = self.timer = None
            return True

    async def close(self):
        if self.token:
            await self.release(self.owner, self.token)

lease = MuteLease()
