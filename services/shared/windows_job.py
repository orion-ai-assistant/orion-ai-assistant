"""Windows process-tree ownership; never infer membership from names or PPIDs.

Jobs have a user-only DACL and no breakaway. Supervisor and stdlib host both
retain handles. Closing the last handle kills only this job, so losing both
controllers cannot strand descendants. Either controller permits recovery.
"""
import ctypes
from ctypes import wintypes as W
import time
from pathlib import Path

import psutil

from .process_lifecycle import (
    A, K, SecurityAttributes, LifecycleError, ProcessReference,
    canonical, digest, native_started, user_key,
)

K.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
K.CreateJobObjectW.restype = W.HANDLE
K.OpenJobObjectW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
K.OpenJobObjectW.restype = W.HANDLE
K.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]
K.QueryInformationJobObject.argtypes = [W.HANDLE, W.INT, ctypes.c_void_p, W.DWORD, ctypes.c_void_p]
K.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
K.SetInformationJobObject.argtypes = [W.HANDLE, W.INT, ctypes.c_void_p, W.DWORD]


class BasicLimits(ctypes.Structure):
    _fields_ = [('per_process_time', ctypes.c_longlong), ('per_job_time', ctypes.c_longlong),
                ('flags', W.DWORD), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('active_processes', W.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', BasicLimits), ('io', ctypes.c_ulonglong * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


def job_name(scope):
    return 'Local\\Orion-Job-' + digest([user_key(), scope])


def metadata(root, component, installation_id, instance_id):
    scope = dict(root=canonical(root), component=component,
                 installation_id=installation_id, instance_id=instance_id)
    return dict(v=1, scope=scope, name=job_name(scope))


class Job:
    def __init__(self, record, *, create=False):
        if (record.get('v') != 1 or not isinstance(record.get('scope'), dict)
                or record.get('name') != job_name(record['scope'])):
            raise LifecycleError('Invalid process job identity')
        self.record, self.handle = record, None
        if create:
            descriptor = ctypes.c_void_p()
            if not A.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    f'D:P(A;;GA;;;{user_key()})', 1, ctypes.byref(descriptor), None):
                raise LifecycleError('Cannot restrict process job')
            try:
                attrs = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
                ctypes.set_last_error(0)
                self.handle = K.CreateJobObjectW(ctypes.byref(attrs), record['name'])
                error = ctypes.get_last_error()
                if not self.handle or error == 183:
                    self.close()
                    raise LifecycleError('Process job creation failed or name already exists')
                limits = ExtendedLimits()
                limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; breakaway is disabled.
                if not K.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    self.close()
                    raise LifecycleError('Cannot enforce process job containment')
            finally:
                K.LocalFree(descriptor)
        else:
            self.handle = K.OpenJobObjectW(0x4 | 0x8, False, record['name'])  # QUERY | TERMINATE
            if not self.handle and ctypes.get_last_error() != 2:
                raise LifecycleError('Process job access denied; ownership unknown')

    def pids(self):
        if not self.handle:
            return []  # Destroyed jobs have neither handles nor live members.
        capacity = 16
        while capacity <= 1048576:
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if K.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
                count = W.DWORD.from_buffer(buffer, 4).value
                return list((ctypes.c_size_t * count).from_buffer(buffer, 8))
            if ctypes.get_last_error() != 234:
                raise LifecycleError('Process job membership unavailable')
            capacity = max(capacity * 2, W.DWORD.from_buffer(buffer).value)
        raise LifecycleError('Process job membership exceeds safety bound')

    @property
    def cancel_path(self):
        scope = self.record['scope']
        from .runtime_layout import state_root
        return state_root(Path(scope['root'])) / scope['component'] / ('cancel-' + scope['instance_id'])

    def cancel(self):
        # Also closes admission when a pending host has opened a handle but has
        # not joined yet. That host must not start an app after stop returned.
        self.cancel_path.touch()

    def contains(self, handle):
        if not self.handle:
            return False
        result = W.BOOL()
        if not K.IsProcessInJob(handle, self.handle, ctypes.byref(result)):
            raise LifecycleError('Process job membership check failed')
        return bool(result.value)

    def root_alive(self, record):
        if record is None:
            return False  # Durable launch intent, never a ready application.
        handle = K.OpenProcess(0x1000, False, record['pid'])
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise LifecycleError('Process access denied; ownership unknown')
        try:
            # A reused PID is not a member of this original job and is not signaled.
            if native_started(handle) != record['native_started'] and not self.contains(handle):
                return False
        finally:
            K.CloseHandle(handle)
        try:
            with ProcessReference(record) as reference:
                if not reference.alive():
                    return False
                if not self.contains(reference.handle):
                    raise LifecycleError('Recorded process is outside its job')
                return True
        except psutil.NoSuchProcess:
            return False

    def records(self):
        records = []
        user = psutil.Process().username()
        for pid in self.pids():
            try:
                record = ProcessReference.capture(pid)
                with ProcessReference(record) as reference:
                    # PID reuse between enumeration and opening cannot authorize a signal.
                    if not self.contains(reference.handle):
                        continue
                    if record['user'] != user:
                        raise LifecycleError('Process job member user mismatch')
                    records.append(record)
            except psutil.NoSuchProcess:
                continue
        return records

    def wait_empty(self, timeout):
        deadline = time.monotonic() + timeout
        while self.pids():
            if time.monotonic() >= deadline:
                return False
            time.sleep(.05)
        return True

    def terminate(self):
        self.cancel()
        # The held kernel job, rather than a stale PID list, is the stop authority.
        self.records()  # Fail closed on unreadable membership or another user.
        if self.handle and not K.TerminateJobObject(self.handle, 1):
            raise LifecycleError('Owned process job termination failed')
        if not self.wait_empty(5):
            raise LifecycleError('Owned process job did not stop')

    def close(self):
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
