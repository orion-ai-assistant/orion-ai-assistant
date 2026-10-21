import base64
import unittest

from orion.api import remote_mic
from orion.api.remote_mic import PcLink, PhoneAudioCleaner, PhoneLink, validate_turn_start, pc_key, turn_key


class MemoryBus:
    """Synchronous in-memory stand-in for RedisBus: publish delivers at once."""

    def __init__(self):
        self.keys = {}
        self.subscribers = {}

    def on(self, channel, handler):
        self.subscribers.setdefault(channel, []).append(handler)

    async def publish(self, channel, message):
        for handler in list(self.subscribers.get(channel, [])):
            await handler(message)

    async def get(self, key):
        return self.keys.get(key)

    async def set(self, key, value, ttl, only_new=False):
        if only_new and key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def refresh(self, key, value, ttl):
        return self.keys.get(key) == value

    async def delete_if(self, key, value):
        if self.keys.get(key) == value:
            del self.keys[key]
            return True
        return False


class FakeCanceller:
    """Records the EchoCanceller calls; output is tagged with a leading b'C'."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def _record(self, *call):
        self.calls.append(call)
        if call[0] == self.fail:
            raise RuntimeError('engine failed')

    def arm(self):
        self._record('arm')

    def disarm(self):
        self._record('disarm')

    def start_turn(self, device, preroll, start_time):
        self._record('start_turn', device, preroll, start_time)
        return b'C' + preroll

    def process(self, device, pcm):
        self._record('process', device, pcm)
        return b'C' + pcm

    def end_turn(self, device):
        self._record('end_turn', device)
        return b''


def start(turn_id='t1', **extra):
    return {'type': 'turn_start', 'turn_id': turn_id, 'sample_rate': 16000, 'start_sample': 1000,
            'wake_end_sample': 20000, 'wake_score': .8, 'preroll_samples': 32000, **extra}


class Harness:
    def __init__(self):
        self.bus = MemoryBus()
        self.phone_out, self.pc_out, self.pc_audio = [], [], []

        async def phone_send(message):
            self.phone_out.append(message)

        async def pc_send(message):
            self.pc_out.append(message)

        async def pc_bytes(data):
            self.pc_audio.append(data)
        self.phone = PhoneLink(self.bus, 'user', phone_send, phone_id='phone')
        self.pc = PcLink(self.bus, 'user', pc_send, pc_bytes, pc_id='pc')
        self.bus.on('orion:remote-mic:user:pc', self.pc.on_bus)
        self.bus.on('orion:remote-mic:user:phone', self.phone.on_bus)


class ValidationTests(unittest.TestCase):
    def test_accepts_wake_end_inside_preroll(self):
        self.assertEqual(validate_turn_start(start())['wake_end_sample'], 20000)

    def test_rejects_bad_fields(self):
        for bad in ({'sample_rate': 48000}, {'turn_id': ''}, {'turn_id': 'x' * 65},
                    {'wake_end_sample': 999}, {'wake_end_sample': 40000},
                    {'preroll_samples': 16000 * 5}, {'start_sample': -1}, {'wake_score': 2},
                    {'start_sample': True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_turn_start(start(**bad))


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_phone_arms_only_while_a_pc_session_is_active(self):
        h = Harness()
        self.assertFalse(await h.phone.sync_arm())
        await h.pc.activate()
        self.assertTrue(await h.phone.sync_arm())
        await h.pc.close()
        self.assertFalse(await h.phone.sync_arm())
        self.assertEqual([m['type'] for m in h.phone_out], ['disarm', 'arm', 'disarm'])
        self.assertEqual(h.pc_out, [{'type': 'phone_ready'}])

    async def test_turn_relays_preroll_and_live_audio_in_order(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.sync_arm()
        small = start(preroll_samples=4, start_sample=0, wake_end_sample=3)
        await h.phone.on_text(small)
        await h.phone.on_audio(b'\x01\x00' * 4)
        await h.phone.on_audio(b'\x02\x00' * 2)
        self.assertEqual(h.pc_out[-1], validate_turn_start(small))
        self.assertEqual(h.pc_audio, [b'\x01\x00' * 4, b'\x02\x00' * 2])
        self.assertEqual(h.bus.keys[turn_key('user')], 'phone:t1')

    async def test_audio_outside_a_turn_is_dropped(self):
        h = Harness()
        await h.pc.activate()
        self.assertFalse(await h.phone.on_audio(b'\x00\x00'))
        await h.phone.on_text(start())
        self.assertFalse(await h.phone.on_audio(b'\x00'))  # Odd byte count.
        self.assertEqual(h.pc_audio, [])

    async def test_pc_turn_end_reaches_phone_and_frees_the_turn(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start())
        await h.pc.on_text({'type': 'turn_end', 'turn_id': 't1'})
        self.assertEqual(h.phone_out[-1], {'type': 'turn_end', 'turn_id': 't1', 'reason': 'pc'})
        self.assertIsNone(h.phone.turn)
        self.assertNotIn(turn_key('user'), h.bus.keys)
        await h.phone.on_audio(b'\x00\x00')
        self.assertEqual(h.pc_audio, [])

    async def test_pc_reject_reaches_phone_as_reject(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start())
        await h.pc.on_text({'type': 'turn_reject', 'turn_id': 't1', 'reason': 'wake_not_verified'})
        self.assertEqual(h.phone_out[-1], {'type': 'turn_reject', 'turn_id': 't1', 'reason': 'wake_not_verified'})

    async def test_stale_pc_decision_is_ignored(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start('t1'))
        await h.pc.on_text({'type': 'turn_end', 'turn_id': 'old'})
        self.assertEqual(h.phone.turn['turn_id'], 't1')

    async def test_turn_without_pc_is_rejected(self):
        h = Harness()
        await h.phone.on_text(start())
        self.assertEqual(h.phone_out[-1], {'type': 'turn_reject', 'turn_id': 't1', 'reason': 'no_pc'})

    async def test_invalid_turn_start_is_rejected(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start(sample_rate=8000))
        self.assertEqual(h.phone_out[-1]['type'], 'turn_reject')
        self.assertTrue(h.phone_out[-1]['reason'].startswith('invalid'))

    async def test_second_phone_is_busy_during_a_turn(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start())
        other_out = []

        async def other_send(message):
            other_out.append(message)
        other = PhoneLink(h.bus, 'user', other_send, phone_id='other')
        await other.on_text(start('t2'))
        self.assertEqual(other_out[-1], {'type': 'turn_reject', 'turn_id': 't2', 'reason': 'busy'})

    async def test_phone_disconnect_ends_the_pc_turn(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start())
        await h.phone.close()
        self.assertEqual(h.pc_out[-1], {'type': 'turn_end', 'turn_id': 't1', 'reason': 'phone_disconnected'})
        self.assertNotIn(turn_key('user'), h.bus.keys)

    async def test_phone_timeout_end_reaches_pc(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start())
        await h.phone.on_text({'type': 'turn_end', 'turn_id': 't1', 'reason': 'timeout'})
        self.assertEqual(h.pc_out[-1]['reason'], 'timeout')
        self.assertIsNone(h.pc.turn)

    async def test_pc_leaving_mid_turn_releases_the_phone(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.sync_arm()
        await h.phone.on_text(start())
        await h.pc.close()
        self.assertEqual(h.phone_out[-1]['type'], 'turn_end')
        h.phone_out.clear()
        await h.phone.sync_arm()
        self.assertEqual([m['type'] for m in h.phone_out], ['disarm'])

    async def test_newer_pc_session_takes_over(self):
        h = Harness()
        await h.pc.activate()
        newer_out = []

        async def newer_send(message):
            newer_out.append(message)

        async def ignore(data):
            pass
        newer = PcLink(h.bus, 'user', newer_send, ignore, pc_id='newer')
        h.bus.on('orion:remote-mic:user:pc', newer.on_bus)
        await newer.activate()
        self.assertEqual(h.bus.keys[pc_key('user')], 'newer')
        await h.pc.on_text({'type': 'heartbeat'})
        self.assertEqual(h.pc_out[-1], {'type': 'inactive'})
        await h.phone.on_text(start())
        self.assertEqual(newer_out[-1]['type'], 'turn_start')
        self.assertNotIn('turn_start', [m.get('type') for m in h.pc_out])
        await h.pc.close()  # The old session must not drop the newer one's key.
        self.assertEqual(h.bus.keys[pc_key('user')], 'newer')

    async def test_canceller_gets_exact_preroll_then_live_audio(self):
        h = Harness()
        fake = FakeCanceller()
        h.pc.cleaner = PhoneAudioCleaner(fake)
        h.pc.clock = lambda: 42.0
        h.phone.device = 'device-7'
        await h.pc.activate()
        self.assertEqual(fake.calls, [('arm',)])
        await h.phone.on_text(start(preroll_samples=3, start_sample=0, wake_end_sample=2))
        self.assertNotIn('device', h.pc_out[-1])
        await h.phone.on_audio(b'\x01\x00\x02\x00')
        await h.phone.on_audio(b'\x03\x00\x04\x00')  # Last pre-roll sample + one live sample.
        await h.phone.on_audio(b'\x05\x00')
        self.assertEqual(fake.calls[1:], [('start_turn', 'device-7', b'\x01\x00\x02\x00\x03\x00', 42.0),
                                          ('process', 'device-7', b'\x04\x00'),
                                          ('process', 'device-7', b'\x05\x00')])
        self.assertEqual(b''.join(h.pc_audio), b'C\x01\x00\x02\x00\x03\x00C\x04\x00C\x05\x00')
        await h.pc.on_text({'type': 'turn_end', 'turn_id': 't1'})
        self.assertEqual(fake.calls[-1], ('end_turn', 'device-7'))
        await h.pc.close()
        self.assertEqual(fake.calls[-1], ('disarm',))

    async def test_canceller_failure_falls_back_to_raw_audio(self):
        h = Harness()
        fake = FakeCanceller(fail='process')
        h.pc.cleaner = PhoneAudioCleaner(fake)
        await h.pc.activate()
        await h.phone.on_text(start(preroll_samples=1, start_sample=0, wake_end_sample=1))
        await h.phone.on_audio(b'\x01\x00')
        await h.phone.on_audio(b'\x02\x00')
        await h.phone.on_audio(b'\x03\x00')
        self.assertEqual(h.pc_audio, [b'C\x01\x00', b'\x02\x00', b'\x03\x00'])
        self.assertEqual([c[0] for c in fake.calls].count('process'), 1)

    async def test_without_canceller_audio_is_unchanged(self):
        cleaner = PhoneAudioCleaner(None)
        await cleaner.arm()
        self.assertEqual(await cleaner.start_turn('d', b'\x01\x02', 0), b'\x01\x02')
        self.assertEqual(await cleaner.process('d', b'\x03\x04'), b'\x03\x04')
        await cleaner.end_turn('d')
        await cleaner.disarm()

    async def test_corrupt_audio_message_is_ignored(self):
        h = Harness()
        await h.pc.activate()
        await h.phone.on_text(start(preroll_samples=1, start_sample=0, wake_end_sample=1))
        await h.pc.on_bus({'type': 'audio', 'turn_id': 't1', 'pc': 'pc', 'pcm': '%%%'})
        self.assertEqual(h.pc_audio, [])
        await h.pc.on_bus({'type': 'audio', 'turn_id': 't1', 'pc': 'pc',
                           'pcm': base64.b64encode(b'\x00\x00').decode()})
        self.assertEqual(h.pc_audio, [b'\x00\x00'])


if __name__ == '__main__':
    unittest.main()
