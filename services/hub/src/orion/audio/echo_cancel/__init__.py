"""Hub echo cancellation for the PC microphone mode.

A phone (later also the PC web client) streams its raw mic (PCM16 LE mono 16 kHz) to the Hub; the
Hub removes what the PC loudspeakers play, using the WASAPI loopback of the default output device
as the reference. Chain: DTLN-aec 512 -> LocalVQE v1.2, reference unaligned and at full level; it
only runs during a turn and only while the loopback had sound in the last ~1.2 s, otherwise the
raw mic passes through. Per-phone engine state survives turns so warm-up stays short.

Relay contract (one `EchoCanceller` per Hub):

    canceller = EchoCanceller()
    canceller.arm()                      # PC assistant session armed: start the 3 s loopback ring
    clean = canceller.start_turn(device_id, preroll_pcm, start_time=None)   # cleaned pre-roll
    clean = canceller.process(device_id, chunk_pcm)                          # per live chunk
    clean = canceller.end_turn(device_id)                                    # tail flush
    canceller.disarm()                   # session disarmed: stop the loopback capture

Output is sample-aligned with the input (output sample k belongs to input sample k, so
`wakeEndSample` keeps its meaning); `process` may return fewer bytes than it was given while the
reference catches up, and `end_turn` returns the rest, so the totals match. The calls are
CPU-bound (the pre-roll takes ~0.4x its duration): call them off the event loop, in order per
device, e.g. `await asyncio.to_thread(canceller.process, device_id, chunk)`. A lost network
packet must be replaced by the same number of zero samples, or the reference pairing shifts.
Until the models are downloaded (first arm) or if anything is unavailable, turns pass the raw
audio through unchanged.
"""
from .service import EchoCanceller

__all__ = ["EchoCanceller"]
