"""Keep only what the live version of a GitHub Release feed needs; delete the rest.

    .tmp\\stage05-tools\\Scripts\\python.exe scripts\\release\\prune_feed.py --feed router          # dry run: lists
    .tmp\\stage05-tools\\Scripts\\python.exe scripts\\release\\prune_feed.py --feed router --apply  # deletes

A client reads timestamp.json -> N.snapshot.json -> M.targets.json -> <sha256>.<target name>. Kept:
* every `N.root.json` (clients walk the root chain), `timestamp.json`, the plain bootstrap download and any asset
  whose name this script does not recognise;
* the live snapshot, its targets metadata and every package/layer/bootstrap it lists (that includes the last
  combined Hub/Router packages the feed lists again for bootstraps that predate split packages);
* a release in progress: versions above the live one, and anything uploaded in the last hour (a release uploads its
  packages before its metadata).
Everything else that is ours (older N.snapshot.json / N.targets.json, hash-prefixed ZIPs nothing live lists) goes.
A client that was downloading a deleted package gets a download error: its installed version is untouched and its
next check finds the new release (update_log explains it to the user). release.py runs this after the new timestamp
is live. `--keep`/`--grace-days` keep older versions too, if that is ever wanted.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import subprocess
import sys
import urllib.request

KEEP = 1
GRACE_DAYS = 0
UPLOAD_GUARD = timedelta(hours=1)
METADATA = re.compile(r'^(\d+)\.(snapshot|targets)\.json$')
HASHED = re.compile(r'^[0-9a-f]{64}\.')


def plan(assets, fetch, *, now, keep=KEEP, grace=timedelta(days=GRACE_DAYS)):
    """`assets`: name -> upload time (aware datetime). `fetch(name)` returns that metadata file's JSON.
    Returns (delete, kept_snapshot_versions); raises if the feed looks inconsistent (then nothing is deleted)."""
    snapshots = sorted(int(m.group(1)) for n in assets if (m := METADATA.match(n)) and m.group(2) == 'snapshot')
    current = fetch('timestamp.json')['signed']['meta']['snapshot.json']['version']
    if current not in snapshots:
        raise ValueError(f'timestamp.json names snapshot {current}, which is not on the feed: not pruning')
    live = [v for v in snapshots if v <= current]  # a staged-but-unpublished newer version is not touched below either
    kept = set(live[-keep:])
    for older, newer in zip(live, live[1:]):
        if now - assets[f'{newer}.snapshot.json'] < grace:  # `older` was current until `newer` went up
            kept.add(older)
    staged = [v for v in snapshots if v > current]  # a release in progress (or one that stopped before its timestamp)
    needed = {'timestamp.json'} | {f'{v}.snapshot.json' for v in [*kept, *staged]}
    for version in [*sorted(kept), *staged]:
        targets_version = fetch(f'{version}.snapshot.json')['signed']['meta']['targets.json']['version']
        name = f'{targets_version}.targets.json'
        if name not in assets:
            if version in staged:
                continue  # its targets are still on their way up
            raise ValueError(f'{version}.snapshot.json names {name}, which is not on the feed: not pruning')
        needed.add(name)
        for target, info in fetch(name)['signed']['targets'].items():
            needed |= {f'{digest}.{target}' for digest in info['hashes'].values()}
    delete = set()
    for name in assets:
        if name in needed or now - assets[name] < max(grace, UPLOAD_GUARD):
            continue  # recent uploads stay too: a release in progress uploads its packages before its metadata
        match = METADATA.match(name)
        if match:
            if int(match.group(1)) < min(kept):  # never a version above what is live
                delete.add(name)
        elif HASHED.match(name) and name.endswith('.zip'):
            delete.add(name)  # listed by no kept version: no client can be on its way to it
    return delete, sorted(kept)


def online(repo, tag):
    out = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo, '--json', 'assets'],
                         capture_output=True, text=True, check=True).stdout
    return {a['name']: datetime.fromisoformat(a['updatedAt'].replace('Z', '+00:00'))
            for a in json.loads(out).get('assets', [])}


def main():
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
    from publish_release import FEEDS, feed_url
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--feed', choices=sorted(FEEDS), required=True)
    parser.add_argument('--keep', type=int, default=KEEP, help=f'newest live snapshot versions kept (default {KEEP})')
    parser.add_argument('--grace-days', type=float, default=GRACE_DAYS,
                        help=f'also keep versions superseded less than this long ago (default {GRACE_DAYS})')
    parser.add_argument('--apply', action='store_true', help='delete (default: only list what would go)')
    args = parser.parse_args()
    if args.keep < 1:
        parser.error('--keep must be at least 1')
    repo, tag = FEEDS[args.feed]['repo'], FEEDS[args.feed]['tag']
    base = feed_url(repo, tag)

    def fetch(name):
        with urllib.request.urlopen(base + name, timeout=60) as response:
            return json.loads(response.read())
    assets = online(repo, tag)
    delete, kept = plan(assets, fetch, now=datetime.now(timezone.utc), keep=args.keep,
                        grace=timedelta(days=args.grace_days))
    print(f'[{args.feed}] {len(assets)} assets; kept snapshot versions {kept}; '
          f'{len(delete)} to delete, {len(assets) - len(delete)} stay.')
    for name in sorted(delete):
        print(('  deleting ' if args.apply else '  would delete ') + name, flush=True)
        if args.apply:
            subprocess.run(['gh', 'release', 'delete-asset', tag, name, '--repo', repo, '--yes'], check=True)


if __name__ == '__main__':
    main()
