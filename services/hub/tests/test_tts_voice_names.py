"""A cloned voice's name (services/tts/engines.py): put right before it is saved, and the one name it is listed,
used and deleted by, also for files saved under an older, broken name."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tts'))
import engines  # noqa: E402


@pytest.mark.parametrize('typed, saved', [
    ('zephyr2 ', 'zephyr2'),                  # a trailing space made a clone that could be listed but not used
    ('  Nika   Kız  ', 'nika kız'),           # one space between words, lower case
    ('İpek', 'ipek'),                         # not "i" + a dot mark, which no lookup matched
    ('Süleyman', 'süleyman'),           # a phone keyboard's "u" + dot mark is one letter
    ('ses/1:*?', 'ses1'),                     # characters a file name cannot hold are dropped
    ('..gizli..ses..', 'gizli.ses'),          # no ".." and nothing but a letter or digit at either end
    ('-_ ses _-', 'ses'),
    ('a' * 80, 'a' * 64),
    ('  ', ''), ('***', ''),                  # nothing usable left: refused
])
def test_a_name_is_put_right_before_it_is_saved(typed, saved):
    assert engines.clean_voice_name(typed) == saved
    assert engines.valid_voice_name(saved) is bool(saved)


def test_an_old_broken_name_is_listed_used_and_deleted_by_its_clean_name(tmp_path):
    registry = engines.VoiceRegistry(tmp_path)
    registry.service_voices_path = tmp_path / 'none'
    (tmp_path / 'zephyrkız2 _voxcpm2-gguf.pt').write_bytes(b'x')   # saved by an older version, space and all
    (tmp_path / 'om_omnivoice-gguf.pt').write_bytes(b'x')
    assert registry.list_voices('voxcpm2-gguf') == ['zephyrkız2']
    assert registry.voice_exists('zephyrkız2', 'voxcpm2-gguf')
    assert registry.voice_exists('zephyrkız2 ', 'voxcpm2-gguf')    # the name the old list gave works too
    assert registry.find_voice_in_any_engine('ZephyrKız2') == ['voxcpm2-gguf']
    assert registry.delete_voice('zephyrkız2', 'voxcpm2-gguf')
    assert registry.list_voices('voxcpm2-gguf') == []
    assert registry.list_voices('omnivoice-gguf') == ['om']        # another engine's clone is left alone


def test_a_new_clone_is_saved_under_its_clean_name(tmp_path):
    registry = engines.VoiceRegistry(tmp_path)
    registry.service_voices_path = tmp_path / 'none'
    path = registry.save_voice_cache('  Yeni   Ses ', {'ref_wav': b''}, 'voxcpm2-gguf')
    assert Path(path).name == 'yeni ses_voxcpm2-gguf.pt'
    assert registry.list_voices('voxcpm2-gguf') == ['yeni ses']
    with pytest.raises(ValueError):
        registry.save_voice_cache('***', {}, 'voxcpm2-gguf')


def test_deleting_leaves_the_voices_that_came_with_orion(tmp_path):
    registry = engines.VoiceRegistry(tmp_path / 'user')
    registry.service_voices_path = tmp_path / 'bundled'
    registry.service_voices_path.mkdir()
    (registry.service_voices_path / 'hazir_voxcpm2-gguf.pt').write_bytes(b'x')
    assert registry.voice_exists('hazir', 'voxcpm2-gguf')
    assert not registry.delete_voice('hazir', 'voxcpm2-gguf')
    assert (registry.service_voices_path / 'hazir_voxcpm2-gguf.pt').exists()
