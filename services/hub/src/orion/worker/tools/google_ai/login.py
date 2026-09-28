"""Compatibility CLI for opening the shared Orion browser manually."""
import argparse
import asyncio
from urllib.parse import urlencode
from orion.browser.manager import BrowserManager


def session_url(query, verify):
    return 'https://www.google.com/search?' + urlencode({'q': query, 'udm': '50', 'hl': 'tr'}) if verify else None


async def open_login(user_id, verify=False, query='Türkiye’nin başkenti neresidir?'):
    await BrowserManager(user_id).open_manual(url=session_url(query, verify))
    print('ready', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--query', default='Türkiye’nin başkenti neresidir?')
    args = parser.parse_args()
    asyncio.run(open_login(args.user, args.verify, args.query))
