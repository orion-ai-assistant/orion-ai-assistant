import pytest

from services.shared.native_postgres import MEMORY_SETTINGS, memory_settings

GB = 1 << 30


def settings(ram_gb):
    arguments = memory_settings(int(ram_gb * GB), search=True)
    return dict(a.split('=') for a in arguments[1::2])


def test_router_server_keeps_its_small_settings():
    assert memory_settings(32 * GB) == MEMORY_SETTINGS


@pytest.mark.parametrize('ram_gb, shared, cache', [(2, '128MB', '1024MB'), (8, '512MB', '4096MB'),
                                                   (16, '1024MB', '8192MB'), (32, '1024MB', '16384MB'),
                                                   (128, '1024MB', '65536MB')])
def test_shared_buffers_is_a_sixteenth_of_ram_within_limits(ram_gb, shared, cache):
    values = settings(ram_gb)
    assert values['shared_buffers'] == shared
    assert values['effective_cache_size'] == cache


def test_other_memory_stays_modest():
    values = settings(32)
    assert values['maintenance_work_mem'] == '512MB' and values['work_mem'] == '16MB'
    assert settings(8)['maintenance_work_mem'] == '256MB' and settings(8)['work_mem'] == '8MB'
    assert settings(16)['max_connections'] == '50'
