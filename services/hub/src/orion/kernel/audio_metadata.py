import base64
import struct


def duration_ms(audio: dict) -> int | None:
    if isinstance(audio.get('duration_ms'), (int, float)):
        return max(0, round(audio['duration_ms']))
    encoded = audio.get('audio')
    if not isinstance(encoded, str) or audio.get('format', 'wav') != 'wav':
        return None
    try:
        header = base64.b64decode(encoded[:65536])
        if header[:4] != b'RIFF' or header[8:12] != b'WAVE':
            return None
        length = len(encoded) * 3 // 4 - (2 if encoded.endswith('==') else 1 if encoded.endswith('=') else 0)
        offset, rate = 12, 0
        while offset + 8 <= len(header):
            name, size = header[offset:offset + 4], struct.unpack_from('<I', header, offset + 4)[0]
            if name == b'fmt ' and size >= 16 and offset + 24 <= len(header):
                rate = struct.unpack_from('<I', header, offset + 16)[0]
            if name == b'data' and rate:
                return max(0, round(min(size, length - offset - 8) * 1000 / rate))
            offset += 8 + size + size % 2
    except (ValueError, struct.error):
        pass
    return None


def metadata(audio: dict, path: str) -> dict:
    result = {k: v for k, v in audio.items() if k not in {'audio', 'text', 'autoplay', '_live'}}
    result['audio_path'] = path
    duration = duration_ms(audio)
    if duration is not None:
        result['duration_ms'] = duration
    return result
