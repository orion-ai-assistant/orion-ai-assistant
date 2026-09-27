"""Visible browser helper, launched only by an explicit local UI action."""

import argparse
import asyncio
import os
from urllib.parse import urlencode

from .profile import profile_lock


def session_url(query, verify):
    search = 'https://www.google.com/search?' + urlencode({'q': query.strip() or 'Türkiye’nin başkenti neresidir?', 'udm': '50', 'hl': 'tr'})
    return search if verify else 'https://accounts.google.com/ServiceLogin?' + urlencode({'continue': search})


async def open_login(user_id, verify=False, query='Türkiye’nin başkenti neresidir?'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '0')
    from playwright.async_api import async_playwright

    with profile_lock(user_id) as directory:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                str(directory), headless=False, channel='chromium',
                args=['--restore-last-session'],
                chromium_sandbox=True, locale='tr-TR', accept_downloads=False,
                timeout=15000,
            )
            try:
                (directory / '.orion-enabled').touch()
                closed = asyncio.Event()
                context.on('close', lambda _: closed.set())
                print('ready', flush=True)
                page = context.pages[0] if context.pages else await context.new_page()
                # No request filtering here: Google sign-in and CAPTCHA resources must load.
                url = session_url(query, verify)
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                except Exception:
                    if closed.is_set():
                        return
                    # Keep navigation/network errors visible for the user to retry.
                try:
                    await asyncio.wait_for(closed.wait(), timeout=900)
                except TimeoutError:
                    pass
            finally:
                await context.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--query', default='Türkiye’nin başkenti neresidir?')
    args = parser.parse_args()
    asyncio.run(open_login(args.user, args.verify, args.query))
