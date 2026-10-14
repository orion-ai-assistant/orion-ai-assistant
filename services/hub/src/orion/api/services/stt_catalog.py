"""Read STT capabilities over the trusted Router connection, never STT files."""
import aiohttp
from services.shared.environment import get_router_base_urls
from orion.kernel.pinned_router import router_session


async def fetch_stt_catalog(api_key):
    base = get_router_base_urls()[0].rstrip('/').removesuffix('/v1')
    async with router_session(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get(base + '/v1/audio/languages',
                               headers={'Authorization': 'Bearer ' + api_key}) as response:
            response.raise_for_status()
            data = await response.json()
    # Forward only public metadata, even if the upstream returns extra fields.
    return {
        'aliases': data.get('aliases', {}),
        'languages': {code: {'name': entry['name']}
                      for code, entry in data['languages'].items()},
    }


def initial_stt_language(device_language, catalog):
    code = str(device_language or '').strip().lower().replace('_', '-').split('-')[0]
    code = catalog.get('aliases', {}).get(code, code)
    return code if code in catalog['languages'] else 'auto'
