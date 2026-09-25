import pathlib, re

path = pathlib.Path('services/hub/tests/voice_availability.cjs')
data = path.read_bytes().decode('utf-8')

# Fix line 45: local voices ARE populated so notice should be empty
old = "assert.equal(elements['tts-availability'].textContent, 'Orion TTS\u2019ye eri\u015filemiyor.');\nui.currentSettings.tts_enabled = false;"
new = "assert.equal(elements['tts-availability'].textContent, '', 'Voices populated — no notice shown');\nui.currentSettings.tts_enabled = false;"
if old in data:
    data = data.replace(old, new)
    path.write_bytes(data.encode('utf-8'))
    print("Patched!")
else:
    # Try to find what's actually there
    idx = data.find("assert.equal(elements['tts-availability'].textContent, 'Orion TTS")
    if idx != -1:
        print(repr(data[idx:idx+120]))
    else:
        print("Could not find pattern")
