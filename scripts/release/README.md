# Windows release packaging (Stage 05)

Supported target: Windows 10 1903+ x64, CPU host runtime. Hub, Router and Client
have independent package identities and monotonically increasing sequences.
Flutter is the existing web client served by bundled Hub Python. Edge/Chrome is
still the window host. This is not a native Flutter Windows executable.

## Build

Run `windows-packages.yml` with exact 40-character Router and Flutter commits.
Builds are sequential: Flutter web, Router static dashboard, then the three ZIPs.
The workflow runs real candidate installation acceptance after the security suite.
It uploads candidate artifacts; it does not deploy or enable updates.

Local equivalents, from the Hub repository:

```powershell
uv venv .tmp/stage05-tools --python 3.11
uv pip install --python .tmp/stage05-tools/Scripts/python.exe --require-hashes -r scripts/release/requirements.txt
uv pip install --python .tmp/stage05-tools/Scripts/python.exe pytest
python scripts/release/download_inputs.py .tmp/inputs
python scripts/release/build_package.py --source . --release scripts/release/hub.json --inputs .tmp/inputs --postgres .tmp/inputs/postgresql-16.15-1-windows-x64-binaries.zip --output .tmp/candidates
```

Use the peer repository root and `release/package.json` for Router or Client.
Build Flutter with `flutter pub get --enforce-lockfile` and `flutter build web
--release --no-wasm-dry-run --pwa-strategy=none` (no service worker: an old one keeps showing the
previous bundle after an update; `build_package.py` rejects a client that has one); build Router with `npm ci` and `npm run build` first.
No SDK, venv, installed database, identity, model or development-only file is
copied. Newly added source modules must be tracked/staged before a local build.
Only hashed wheel dependencies are installed. Input archive hashes are pinned in
`inputs.json`; Python/Redis hashes were measured from HTTPS vendor archives,
FFmpeg's hash was also supplied by its GitHub release asset, and PostgreSQL uses
the canonical Stage 03 pin. These are provenance inputs, not an independent
vendor signature claim. Archive changes fail closed.

PostgreSQL UTF-8 preparation happens in a fresh build tree before the final ZIP
hash and TUF signature. The package includes its preparation receipt and supplier
licenses. No active or legacy runtime is patched. FFmpeg's upstream license/docs
ship with the Router runtime; its GPL build and corresponding source provenance
remain a release-distribution obligation. Model/LLM/STT/TTS engine downloads are
separate from these host packages. CUDA is not a supported host package profile;
selection rejects it explicitly rather than silently substituting CPU weights.

## Fast release rounds: `build_release.py`

One command builds, packages and accepts, and prints the wall-clock time of every step:

```powershell
.tmp/stage05-tools/Scripts/python.exe scripts/release/build_release.py                 # build + FULL acceptance
.tmp/stage05-tools/Scripts/python.exe scripts/release/build_release.py --accept fast   # build + quick pre-check
.tmp/stage05-tools/Scripts/python.exe scripts/release/build_release.py --accept none   # build only
```

Options: `--serial` (one step at a time, also `--jobs N`), `--no-cache` (rebuild everything, the old
behaviour), `--force-accept` (rerun the full acceptance even for an already accepted set),
`--output <dir>` (default `.tmp/candidates/<three package ids>`; an existing directory is reused only
if the bytes match), `--no-prune`. Bump `version`/`sequence` in the release jsons first, as before.

**What is skipped, and why it is safe** (cache under `.tmp/build-cache`, safe to delete at any time):

* *Flutter web / Router dashboard build*: skipped when the hash of every source file (tracked, new
  unignored and locally edited ones; ignored build output excluded) and of the previous output
  directory equal what the last good build recorded. Any edit, new file or manual rebuild
  invalidates it.
* *Package ZIP*: the key is the content hash of the tracked source files that go into the package
  (working tree, as the builder copies it), the release json, the dashboard/web output, the pinned
  inputs (`inputs.json` + PostgreSQL pin), the lock file and the build tooling itself. Equal key: the
  earlier ZIP is copied byte-for-byte (same sequence **and** same hash, which the updater requires).
  Any difference: rebuilt, never reused. Hub changes whenever `services/shared` changes, so expect
  the Hub to rebuild most rounds; the Router and Client rebuild only when their own sources, the
  dashboard/web output or the release json change.
* *Runtime layer*: Python + locked wheels, PostgreSQL (UTF-8 prepared, `initdb` self-check), Redis,
  FFmpeg are about 90 % of a package and independent of its sources. They are prepared and
  deflated once per input set (`.tmp/build-cache/runtime/<kind>-<key>`, ~1 GB each) and each package
  copies those already-compressed members verbatim. The ZIP is **byte-identical** to compressing the
  whole tree in one pass (same member order, same deflate level; a test and a real-package check
  prove it), so this changes build time, not the artifact. A changed lock file, pin, python/PostgreSQL
  tooling or runtime recipe rebuilds the layer. Text inputs of that key are hashed with CRLF read as
  LF, so a `core.autocrlf` checkout (e.g. a git worktree) and an LF checkout share one layer; the 3 most
  recently used layers per kind are kept, so branches with different locks do not evict each other.
* *Byte-reproducible*: the same commit and inputs give the same ZIP sha256 on any PC. Members have a
  fixed date, mode and order (`zip_layers.py`); sources are staged as git stores them (CRLF->LF for
  files committed as LF, so `core.autocrlf` and editor line endings never reach a package); uv's
  console-script launchers (`site-packages/bin/*.exe`, which embed the absolute build path including a
  random temporary folder, and are never used: Orion runs `python -m`) are removed with their RECORD
  lines. Test: `test_same_inputs_give_byte_identical_package_zips`. This changed the runtime layer, so
  the first Hub and Router built with it differ from earlier builds: release them under a new sequence.

Steps run concurrently (Flutter, dashboard and Hub packaging together; Client and Router as soon as
their web output exists) with at most `--jobs` (default 3) at a time; each package builds in its own
temporary directory and logs to `.tmp/build-work/<timestamp>/logs`. `--serial` disables this.
While Flutter is actually compiling (dart2js is the critical path, ~50-60 s idle on a 6-core PC), the
other steps run at below-normal CPU priority: on an idle machine they still use the free cores, on a
busy one they no longer stretch dart2js (seen: 130-260 s next to Next.js, packaging and another
session's acceptance). Flutter's own defaults are already the fast release ones (`-O4`, minified, no
source maps, `pub get` skipped when the lock matches); `-O1` measured 6 s faster and 4 % larger, not used.

When Flutter is still compiling once Hub and Router are packaged, the full acceptance starts right away
(`accept_packages.py --client-wait <marker>`): fresh install of Hub+Router, standalone Router cycle, then
it waits; when the Client package exists it is added through the product's add path (TUF metadata v2 of
the same test repository, real verification + safe extraction) and the Hub/Client steps run. It is still
one full, publishable `result.json` with `packages` = the exact three hashes; `install` names the path
used. Trade-off: in such a round the one-shot install of all three is not what is exercised (it is in every
round where Flutter does not compile). Measured on the owner's PC: Flutter alone 64.5 s; serial round
~150 s; overlapped 143 s (Flutter slows to 81 s next to the install, ~60 s remain after the Client).
Running the acceptance at below-normal priority was slower (179 s), so it runs at normal priority.

### Acceptance modes

`accept_packages.py --mode full|fast` (also what `--accept` passes) always says which one ran.

* **full** (default): installs the exact Hub+Router+Client set from signed test metadata and
  exercises it as before (real TUF verification, safe extraction, fresh Hub `initdb`, standalone
  Router, registered start, Flutter health, Router bootstrap, DB row + encrypted override surviving a
  restart, immutability: the installer's sha256 inventory and an independent re-hash after the
  restart must both equal the signed ZIP's members, hashed from the candidate while it installs). It is the **only** result `publish_release.py` accepts
  (`write_acceptance` refuses `mode: fast`; `release_acceptance.py` still binds the exact hashes). With
  `--reuse-accepted` (set by `build_release.py`) it is skipped only when this exact package set already
  passed a full run and the acceptance tooling (`services/shared`, `scripts/release`, `installer/app`
  without tests) is unchanged; the output says so and names the earlier result.
* **fast**: an additional pre-check, never a replacement. It compares the ZIPs with the last passing
  full run and installs only what changed plus what it needs to run (Router: Router alone;
  Hub or Client: Hub+Client together; the Router only when it changed).
  It writes `mode: fast`, `success: false`, `fast_success: true`; it cannot be published. With no
  earlier full run it simply runs the full set.

Trade-off: a fast pass does not prove the whole set. A Hub-only change is not run against the Router
(the Hub-to-Router bootstrap check and the standalone Router cycle are skipped), a Router-only change
is not run against the Hub. It does not skip `initdb` or the immutable-package checks of what it does
install; it saves the unchanged components' extraction, hashing and start/stop cycles. The full run
stays the gate: run it once before publishing. A full run is also cheaper than before without
weakening anything (~150 s -> ~85 s on the owner's PC): the installer hashes and extracts with
threads (`sha256_many`, `extract_zip`), which overlaps the virus scanner's first-open latency on
freshly extracted files (the sequential inventory hash alone took 75 s of a 104 s install), and
the pre-start fingerprint pass became the signed-ZIP comparison above. What is left is real
product work for the whole set: install ~25 s (extraction, inventory, Hub `initdb` ~6.5 s), Router
standalone cycle ~15 s, launcher start ~14 s, stop/restart with persistence ~18 s, re-hash ~5 s.
Nothing package-dependent is reused from an earlier run (no initdb or extraction template): those
are exactly what a changed package can break; `--force-accept` and a normal full run do the same work.

### Disk hygiene: `lifecycle-tests`

Each acceptance run leaves a ~1.3 GB sandbox `stage05-<random>` under
`%LOCALAPPDATA%\Orion\lifecycle-tests`. Every run starts a detached prune that keeps the newest 3,
the sandbox of the last passing full acceptance (what a pending publish points to), any sandbox
containing a file named `PINNED`, and anything younger than one hour; the rest is deleted. By hand:

```powershell
.tmp/stage05-tools/Scripts/python.exe scripts/release/accept_packages.py --prune --dry-run --scratch-base "$env:LOCALAPPDATA\Orion\lifecycle-tests"
.tmp/stage05-tools/Scripts/python.exe scripts/release/accept_packages.py --prune --keep 2 --scratch-base "$env:LOCALAPPDATA\Orion\lifecycle-tests"
# also trim the older product-*, stage04-*, router-access-pg-* families:  add --all-prefixes
```

To keep a sandbox for good: create an empty file `PINNED` inside it. The build cache lives in
`.tmp/build-cache` (runtime layers ~2 GB, newest two ZIPs per kind); delete it to force a clean build.

### Windows Defender exclusions (optional, developer PCs only, administrator PowerShell)

`scripts/dev/setup-defender-exclusions.ps1` adds (or with `-Remove` undoes; `-WhatIf` only lists) the
exclusions below plus the Router and Flutter repositories, `~/.orion-lifecycle-tests` and the Pub/npm
caches. Nothing runs it automatically and it changes nothing for end users. By hand:

Real-time scanning inspects every file open, and a build or acceptance run opens tens of thousands of
small files (extracting ~1 GB of PostgreSQL, Python and wheels, hashing, starting `postgres.exe`).
Excluding the two work folders is the single biggest remaining saving. This is a security setting:
run it yourself, in an elevated PowerShell, and only if you accept the trade-off for these folders.

```powershell
Add-MpPreference -ExclusionPath "D:\krstlcm Workspace\DevProjects\orion-ai-assistant\.tmp"
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\Orion\lifecycle-tests"
Get-MpPreference | Select-Object -ExpandProperty ExclusionPath      # verify
# undo: Remove-MpPreference -ExclusionPath "<same path>"
```

Both folders only hold build output and throwaway test sandboxes; nothing from them is executed
outside the isolated acceptance run. Measured effect (profile of one package build, no exclusion
active): about 35 % of a package build was file open/stat time inside the scanner. Expect the cold
runtime-layer build and the full acceptance install/start steps to drop by roughly a quarter to a
third; incremental rounds (mostly hashing and 1-2 small ZIPs) gain little.

## Trust and signing

Use [python-tuf](https://theupdateframework.readthedocs.io/en/latest/api/tuf.ngclient.updater.html),
not a bespoke signature protocol. `ReleaseClient` requires a root delivered out
of band by the trusted installer. HTTPS repository URLs cannot contain passwords.
The TUF cache preserves metadata version/expiry checks. The client additionally
records the last verified product sequence/hash, rejects product rollback and
same-sequence substitution even under newer signed metadata, and retains the last
successful check time when offline or when verification fails. `checked` is not
an assertion that the installed application is current. Stage 06 must validate
the entire proposed set with `compatible(..., installed_sequences=...)`, including
unchanged components, and commit installed state only after its own transaction.

Production root policy: at least 2-of-3 offline Ed25519 root keys, separate from
targets/snapshot/timestamp keys. Store only the public signed root in the trusted
installer. Never put private keys in Git, packages, logs or the user data root.
`publish_metadata.py` uses explicitly supplied role keys and verifies every role's
configured signature threshold before writing output. No production root is
generated automatically. The test runner's ephemeral keys and `TEST-ONLY` root
are fixtures and cannot be used as release credentials.

Default expirations: root is provisioned externally (recommended one year),
targets 30 days, snapshot 7 days, timestamp 1 day. Repository metadata versions
must increase. Rotate roots using TUF's old AND new root thresholds, retaining
every sequential `N.root.json`. Retain old consistent-snapshot metadata/targets
when deploying the new staging repository; never replace live repository history
with just one output directory. Never reuse a package version/identity for changed
final bytes. A re-signed unchanged target keeps its hash. A rollback is an explicit
same-schema recovery decision for Stage 06, not a normal catalog downgrade.

`sign-package-metadata.yml` requires a successful candidate workflow, the protected
`orion-release-signing` environment, `ORION_TUF_ROOT_JSON`, and
`ORION_TUF_ROLE_KEYS_JSON` (role -> key ID -> PEM). No offline root private keys
enter CI. Its output is a signed metadata artifact and an installer ZIP; there
is no server upload/publish action. Initial installer distribution needs an
authenticated delivery channel; TUF cannot establish its own initial root over
the same untrusted channel. Authenticode and production hosting are not claimed.

## Install and prepare

`build_bootstrap.py --trusted-root <public-root> --inputs <cache> --output <zip>`
assembles bundled Python + verifier + immutable public trust anchor. On a clean
Windows machine, this trusted bootstrap runs `Install-Orion.ps1` with explicit
metadata/target URLs and target filenames. Python, Git, Node and Flutter SDK are
not needed by that entrypoint. Default root/registry are under LocalAppData/Orion.
It uses the existing schema-1 installation registry (optional `package` receipt), fixed entry
`bootstrap/Orion.ps1`, operation lock, private path checks and native lifecycle.
It refuses an existing registry or occupied component roots. Standalone Router
can be installed without Hub/Client and started through `Run-Router.ps1 -Registry
<install.json>`. This is a new-install path only. A failed setup retains its
registry/data for explicit repair; do not rerun over or delete an existing DB.

Packages live in `Hub|Router|Client/releases/<package-id>`; `data` and `state` are
outside the release tree. Within data, the old relative layout is preserved:
Hub's `.local_db/postgres/data`, `services/hub/data`, `services/.env.global.local`
and `files/security`; Router's `.pgdata-prod`, `.env` and `persistent`. In particular
Hub `router-access-seed`, TLS/UUID, account receipts and encrypted per-account,
per-Router `.manual` files are never package inputs. Local model paths remain in
the registry's `locations.models`. Legacy source roots retain their existing
layout. No source/DB/runtime conversion is attempted.

After TUF verification, `state/installed-package.json` records the final extracted
file inventory. Its SHA256 and the verified archive's identity/hash/length are
pinned in the private installation registry's optional `package` object. The
launcher and installed Router inspection check this inventory before trusting
package provenance. An arbitrary local receipt is not accepted without that
installed record. This is local installation state following signature validation,
not an additional signature protocol. The original public bootstrap root is kept
beside the stable launcher for later stages. Router UUID/SPKI/public data paths
are recorded after first identity initialization, before any service launch.

Router API generation 2 explicitly includes `GET /api/v1/hubs/accounts` and
`GET /api/v1/hubs/account-status`. New Hub requires Router generation 2; old
mobile generation 1 remains in the declared Hub range. Source `pyproject.toml`
versions are not the signed package authority. The package manifest is.

`ReleaseClient.prepare()` only downloads/verifies/extracts into a fresh tree. It
does not execute or activate packages. It rejects unsafe Windows paths, links,
case collisions, special entries and expansion beyond 8 GiB / 100,000 members.
It refuses existing destinations and checks the embedded manifest against signed
TUF custom metadata. Before extraction it copies cache bytes into an exclusive
private snapshot, bounds the copy by the signed length and verifies/extracts that
same stream. Replacing or modifying the download cache cannot change verified
extraction bytes. Windows TEMP with group write access is not an executable
staging area; security fixtures use private Orion AppData as the real installer does.
The installer also validates a private registry parent and rejects relative or
linked authority paths before preparing packages. Packaged configuration loads
defaults, then external data overrides, while explicit process values win.
The embedded runtime uses Windows separators in `_pth` and a sourceless startup
hook to disable bytecode writes and configure UTF-8 standard streams. These
settings are verified before the final ZIP and signature; installed runtimes are
never patched. Process state records the installed package ID.
Legacy Router's Git/pip updater and source dashboard rebuild
are blocked in packaged mode. Auto updates and Windows Startup stay disabled.

## Acceptance boundary

`accept_packages.py --artifacts <candidates> --inputs <input-cache> --scratch-base
<isolated-Orion-AppData>` creates a local TUF repository with ephemeral fixture
keys, builds an SDK-free installer, installs the actual ZIPs into Unicode paths,
starts/stops standalone Router then the combined PowerShell launcher, checks Flutter health,
DB/seed/manual-key persistence and unchanged package hashes. All product commands
use bundled Python and a System32-only PATH. This is a development-host package
integration test. It is **not** a clean-machine/clean-VM or real logon/window test.

Stage 01/03/04 open acceptance remains open; Stage 05 does not authorize Stage
06–08 implementation or production rollout.

## Stage 06 explicit local update

Historical Stage 06 scope is below. The user subsequently authorized Stage 07
development while keeping Stage 06 acceptance debts open; see the Stage 07 section.

17 October user authorization adds Stage 06 only. Flutter remains web; no native
Flutter Windows EXE or public production installer is claimed. See
[manual web trial](../../docs/orion-lifecycle-plan/06-manual-test.md).

Retain the trusted installer ZIP separately from Hub releases. A newly built
bootstrap contains `scripts/release/update_local.py`, the helper and its bundled
Python/dependencies. Extract a new bootstrap alongside the old one; never replace
the only running recovery copy. Trust-root changes still require TUF rotation.
Since bootstrap sequence 1 the retained bootstrap updates itself through the signed feed
(see "Retained bootstrap self-update" below); it is not an unsigned channel.

From that retained bootstrap directory, using its bundled runtime:

```powershell
./runtime/python/python.exe -B -m scripts.release.update_local --registry <absolute-install.json> prepare --metadata-url https://<host>/metadata/ --targets-url https://<host>/targets/ <signed-target.zip>
./runtime/python/python.exe -B -m scripts.release.update_local --registry <absolute-install.json> resume
./runtime/python/python.exe -B -m scripts.release.update_local --registry <absolute-install.json> status
./runtime/python/python.exe -B -m scripts.release.update_local --registry <absolute-install.json> rollback
```

These are interface examples, **not a published download command**. The trust root
is fixed at `<registry-parent>/bootstrap/trusted-root.json`. No arbitrary executable,
script, archive folder or root override is accepted by this CLI. It accepts owned
registered packaged Hub/Client only. Standalone Router update/conversion remains
Stage 08. Changing schema versions is rejected before package preparation.

`prepare` verifies the complete proposed set including unchanged Router/Client and
mobile API 1, stages immutable sibling releases and retains inventories. It leaves
active code/data untouched. If a prior attempt left the same immutable target,
it is compared with a fresh TUF-verified extraction in private temporary staging;
matching bytes can be reused, changed bytes require repair. Existing releases are
never patched or deleted for retry. Create a fresh `ReleaseClient` for each check
(python-tuf Updater refresh is one-shot); keep its on-disk cache for trust/high-water
state. `resume` waits while a registered service is running;
it does not interrupt active work. For the Stage 05 baseline, explicitly stop the
installation when convenient, then resume. Automatic live drain/scheduling is not
enabled. A proposed Hub must declare `lifecycle.update-quarantine.v1`, including
for Client-only updates. This prevents starting an older Hub that lacks admission
quarantine during health checks. A component whose manifest also declares
`lifecycle.update-admission-in-place.v1` (the Hub, with the Client it serves) keeps
running after a passing health check: the final verify and the result record happen
while admission is still closed, then `completed` opens admission where it runs and
the launcher only attaches (window, tray). Others (today's Router, which checks the
gate only at its start) end health stopped and are started normally.

The schema-1 journal and pointer live in `<registry-parent>/updates/<UUID>/journal.json`
and `updates/active.json`. Completed release inventories and the registry are
replayable publications behind the admission barrier. Lifecycle process/Job records
embed a code root: each updated release gets external
`<component>/state/releases/<package-id>`; the old state and user data are preserved.
The stable PowerShell entry and current launcher refuse normal launch during
switch/health/repair. Candidate HTTP/WebSocket traffic is denied except health and
public TLS identity; Worker pending recovery and background provisioning/browser
restore wait for admission. Health runs the real native API/Worker/DB/web client
and checks Router UUID/SPKI. It ends with services stopped, ready for an explicit
launch; no user jobs are replayed by the helper.

Offline data backups are copied only under verified stopped lifecycle locks,
flushed and hash checked. Automatic rollback selects same-schema previous code and
inventory; it never restores an old DB snapshot over candidate/user writes.
Health retries are bounded at two. A lock/access/ownership failure leaves
the installation closed. The candidate runtime probe has a 180-second bound;
package inventory verification is part of this work. After a stop request the
helper waits up to 15 seconds on the verified supervisor process reference before
rechecking stopped ownership, rather than assuming its stopped journal means its
OS process has already exited. Failure leaves
`repair_required` with admission closed. `rollback` retries explicit code recovery
after resolving the local fault; automatic `resume` does not loop in repair.
Transactions, previous releases, partial staging and backups are retained; no
automatic cleanup or uninstall is introduced.

Run `test_update_helper.py` for TUF-backed transaction fault tests, real helper
process exits and OS lock contention. `accept_update.py --previous-artifacts ...
--update-artifacts ... --inputs ... --scratch-base <private-AppData-test-root>`
runs a fresh real package install/update/restart with SDK-free PATH. It requires
previous candidate ZIPs and a higher-sequence new Hub candidate. It uses ephemeral
fixture signing only, never production keys or the manual/user installation.
`--retry-fixture <failed-test-root> --inputs <cache>` is limited to a stopped,
rolled-back ephemeral fixture. It retains the original root and signed repository,
builds recovery-v2 alongside the first bootstrap, re-proves the same target, and
checks update/restart, DB/manual key, model/security hashes and complete shutdown.
`--audit-fixture <completed-test-root>` repeats inventory/data protection and
restart/cleanup checks for a completed ephemeral fixture after a test-driver
interruption. The restart adapter holds verified supervisor references before
normal shutdown and waits for their OS exit; it never treats a stopped journal
alone as evidence that the last process has exited.

## Stage 07 automatic staging and safe launch

Stage 07 adds source implementation, not production rollout or Stage 08. New
installs still default to automatic updates off until release acceptance. Existing
manual trial packages are unchanged. See [results](../../docs/orion-lifecycle-plan/07-automatic-updates-and-flutter.md)
and [trial instructions](../../docs/orion-lifecycle-plan/07-manual-test.md).

The trusted installer may supply `--release-source <private-json>` to
`build_bootstrap.py`. Its exact schema is:

```json
{
  "schema": 1,
  "channel": "stable",
  "metadata_url": "https://<release-host>/metadata/",
  "targets_url": "https://<release-host>/targets/",
  "targets": {"hub": "stable/hub.zip", "client": "stable/client.zip"}
}
```

These are placeholders, not available production addresses. Target names must
refer to real TUF-signed packages. Feed membership is installer policy; packages
still pass signed metadata, monotonic sequence, hash, API and schema checks.
Only Hub/Client targets are accepted. Every published immutable package needs a
new version/package ID/sequence; never republish changed bytes under the old ID.

New package installation from the retained bootstrap records
`bootstrap/automatic-runtime.json {schema:1,root:<retained-bootstrap>}` and copies
the optional `release-source.json` beside the installed trusted root. The retained
directory must be private and remain available independently of Hub releases.
Source installations do not invent this pointer. Existing trial installations
are not silently migrated, and the sole/running bootstrap is never overwritten.

The outer launcher invokes the retained runtime's fixed
`scripts/release/update_automatic.py --registry <absolute-install.json> startup`
before native start, then spawns `watch` after normal startup. `check` is also a
fixed local CLI action and respects the same six-hour throttle. No arbitrary
command, script, package path, feed URL or trust root is accepted by the HTTP API.
Watcher ownership is per installation with an OS lock. It wakes every five minutes,
checks at most every six hours, stages only, and exits on the next poll after all
services are proven stopped. It is spawned outside the Hub-owned Job; containment
and breakaway rules for product children are unchanged.

The next launch applies staged updates only when the preference is on, deferral
expired and the existing Stage 06 stopped gate succeeds. It never stops running
user work. A critical/repair journal blocks normal startup and uses the explicit
Stage 06 recovery CLI. A stopped-only activation may run unchanged dependencies
for the existing health probe; it does not update their code or independent policy.
Turning off automation does not abandon an already critical transition.

Preferences and operation locks serialize admission. Registry comparison permits
only current boolean preferences to differ from transaction snapshots; publication
and rollback preserve them. `updates/policy.json` stores manual network pause and
24-hour deferral, while `updates/automatic.json` stores check timestamps and a
sanitized error class. Pausing affects new network work; a prepared update can
still apply at safe launch. Metered networks require manual pause. A preference
write during a locked download returns 409 for retry; started preparation can finish.

Authenticated `GET /api/v1/updates` is available to normal Hub clients. `PUT` only
accepts `automatic_updates`, `paused`, `defer_hours` (0 or 24), and requires the
actual desktop peer plus a permitted origin. A phone JWT, paired token, forwarding
header or mobile listener cannot grant PC management. Router independent updates
remain Stage 08. Flutter keeps last-known status/time on connection failure and
discards stale replies on Hub/account changes.

`test_automatic_updates.py` uses signed, disposable local fixtures and a fake native
health host. It covers live-use staging, preference admission, deferral, network
failure/retry, next-launch activation, rollback preferences and interrupted staging.
These are not a new real package/DB/Windows Job acceptance. That acceptance,
real active chat/voice/transfer/remote clients, clean VM, power-loss and production
hosting/signing remain open; previous Stage 01/03/04/05/06 debts remain recorded.

## Simple single-key releases (GitHub Releases feed)

For a small project one Ed25519 key signs everything (TUF threshold 1). Create it once:

```powershell
.tmp/stage05-tools/Scripts/python.exe scripts/release/release_keys.py init
```

The encrypted private key goes to `~/.orion-release/release-key.pem` (never in Git; **back it up**,
losing it means existing installs cannot trust new releases). Commit only
`scripts/release/trusted-root.json`. Per release: bump `version` and `sequence` in each component's
release json, build the packages, run `accept_packages.py`, then:

```powershell
.tmp/stage05-tools/Scripts/python.exe scripts/release/publish_release.py --artifacts .tmp/candidates --accepted-result <accept result.json> --inputs .tmp/inputs
```

Add `--dry-run` to stage without uploading. Upload needs the `gh` CLI signed in. Packages are published
under version-free names (`orion-hub-windows-x86_64-cpu.zip`, ...); installs and updates compare the
signed `sequence`, identically for Hub, Router and Client (Router keeps its own on/off preference).
Single-key metadata is valid for one year: publish at least once a year, even with no code change.
The `preview` channel binds the exact packages to the automated isolated acceptance only; clean VM,
logon and power-loss evidence remain required for `beta`/`stable`.
Users install with `irm https://raw.githubusercontent.com/orion-ai-assistant/orion-ai-assistant/main/get-orion.ps1 | iex`
or `get-orion.cmd`.


## Retained bootstrap self-update

The retained bootstrap (`%LOCALAPPDATA%\Orion\Bootstrap-Installer`: own Python plus copies of
`services/shared` and `scripts/release`) runs checks, the watcher and the apply/rollback helper outside
the Hub, so it used to be frozen at install time. It is now a signed TUF target, component `bootstrap`,
target name `orion-bootstrap-windows-x64.zip` (the same stable name `get-orion.ps1` downloads; the plain
release asset and the hash-prefixed target are the same bytes). Only `ReleaseClient` (signature, hash,
metadata/product rollback protection, safe extraction) is the trust path.

* Version identity: `scripts/release/bootstrap.json` (`sequence`, `version`, `package_id`
  `orion-bootstrap-<version>-windows-x86_64-cpu`). `build_bootstrap.py` copies it into the bundle as
  `orion-release.json` and records `sequence`/`package_id`/`version` in the bundle's `bootstrap.json`
  (no `sequence` = built before self-update = 0) and writes `<output>.target.json`. Bundles are
  byte-reproducible (fixed zip timestamps, no `.pyc`).
* `publish_release.py` builds the bundle into the signed artifact set. It refuses a bundle whose contents
  changed while its `sequence` was already published, or whose sequence is lower than the feed's
  (`bootstrap_gate`). To publish a Hub-only release without touching the bootstrap, pass
  `--reuse-bootstrap <previous orion-bootstrap-windows-x64.zip>` (its `.target.json` must sit beside it;
  both stay in `.tmp/release/v<N>/artifacts`). The bundle is not part of the isolated package acceptance
  run; it is bound to the signed set but only checked by the self-update validation below.
* Adoption (`services/shared/bootstrap_update.py`, called from `AutomaticUpdates.check`, hub scope,
  before the component check): newer signed `sequence` than the active bootstrap's, no update/recovery
  transaction active (`helper.status()` terminal, `update_gate.blocked` false). Under the installation
  operation lock the bundle is prepared in a fresh sibling folder
  `Bootstrap-Installer-s<sequence>-<id>` (the running bootstrap is never overwritten) and validated:
  required files, bundle metadata equals the signed release, `trusted-root.json` byte-equal to the installed
  `bootstrap/trusted-root.json` (a root change stays a separate rotation), a test-only bundle never replaces a
  production one, and the bundle's own `runtime/python/python.exe` must import the scripts and answer
  `update_automatic.py --help`. Only then `bootstrap/automatic-runtime.json` is atomically repointed (its
  schema is unchanged). User data, registry and the trusted root are never touched. Any failure removes the
  new folder and stores a fixed class in `automatic.json` `bootstrap_error`; Hub/Client/Router checks continue.
* Hand-over: the watcher notices the pointer no longer names its own bundle, returns (releasing the watcher
  lock), starts the new watcher through the pointer, and waits up to 60 s for it to prove it started
  (`bootstrap/self-update.json` `started_at`). If it does not, the pointer returns to the previous bootstrap
  (`rolled_back`, `error: NewBootstrapDidNotStart`) and the old watcher keeps polling. The failed sequence is
  not retried; a higher one is. `runtime_command` also falls back automatically when an unconfirmed new
  bootstrap folder is missing or incomplete.
* Trial: the previous bootstrap stays until the new one completes one successful check (`confirmed`). Then a
  previous bootstrap that this mechanism created (marked `.orion-self-updated`) is deleted; the original
  manually extracted folder is never deleted. Adoption is never stacked on an unconfirmed trial.
* `automatic.json` `error`: if the Hub-triggered `check --now` process exits non-zero, the Hub records
  `BootstrapIncompatible` (exit 2: argparse refused the arguments, i.e. the retained bootstrap predates
  them) or `CheckProcessFailed`. `GET /api/v1/updates` also reports `bootstrap: {sequence, state, error}`.
* One-time limitation: an install whose bootstrap predates this feature cannot adopt it (it runs the old
  code). Run `get-orion.ps1` (or extract the new bootstrap ZIP beside the old one and run its
  `Install-Orion.ps1` flow) once; from then on bootstraps update themselves.

Publish order for a bootstrap change: bump `scripts/release/bootstrap.json` (`sequence`, `version`,
`package_id`) -> build/accept packages as usual -> `publish_release.py ...` (it builds, gates and signs the
bundle with the packages).


## Explicit "update now" and the tray icon

* **Update now** (`services/shared/update_now.py`, `POST /api/v1/updates/apply`, desktop peer only, same
  `local_control` rule as the preferences; `POST .../apply/cancel` until the watcher claims). The automatic
  next-safe-launch path is unchanged. The Hub waits for three quiet readings of `orion/kernel/activity.py`
  (chat/tool turns from `chat:state:*`, short Redis leases for dictation and media transfer, remote-session
  markers), closes admission (`orion:update:draining`, new chats/dictation get 503), re-checks and hands over
  through `updates/apply-now.json` (`waiting_for_idle` -> `requested`). The retained bootstrap's watcher (outside
  the Hub Job) claims it, stops the services (`launch(stop=True, keep_window=True)`), runs the normal
  `UpdateHelper.resume` (backup, switch, health, automatic rollback) and restarts everything. The Flutter card
  polls through the outage and reloads the page when the state is `completed`. Unclaimed (90 s), abandoned or
  stalled requests become an explicit `failed` with a fixed error code. Needs a running watcher
  (`GET /api/v1/updates` `apply_now`); without one the request is refused and the next launch applies the update.
  **Who owns the operation lock (fixed in this stage).** `process_request` takes `operation_lock(registry)` *before*
  it stops anything and keeps it through stop -> `helper.resume` -> outcome record; services restart only after
  the lock is released, with `launch(apply_owner=True)` (opens the app window, never re-applies). Before this, the
  lock was free between the launcher's stop and `resume`; any other launcher in that window (tray "Open", a second
  start, Windows startup: `launch()` -> `run_startup` -> `startup` -> `resume(automatic=True)` holds the lock for the
  whole apply) made `resume` raise `LifecycleError: Lifecycle busy` (a `RuntimeError`, hence `error=RuntimeError`),
  leaving the journal `waiting_for_idle`, the Hub stopped and the watcher restarting Orion. Now: a busy lock is waited
  for (300 s) *before* anything is stopped (`OperationBusy`, services untouched); `launcher.wait_for_update` waits while
  `update_now.in_progress()` (watcher alive, phase stopping/applying/restarting, not stale >10 min) as well as for
  journal apply phases; another launcher that already applied exactly this transaction is reported as `completed`.
  Test: `test_update_now_apply.py` (a rival thread takes the lock in a loop; it never gets it between stop and the end
  of the transaction, for success and for rollback).
* **Watcher robustness + log.** The watch loop used to die silently when a manual check (Hub refresh button) held
  `automatic.json` while the watcher's own 5 s poll tried the same lock (`LifecycleError`, treated as "another
  watcher exists"). Each loop step now handles its own errors. `services/shared/update_log.py` writes
  `<install>\updates\watcher.log` (256 KiB, 2 rotated backups) for the watcher, the check process, the Hub-triggered
  check and the launch-time apply: events (`watcher.start`, `check.start/staged`, `apply-now.*`, `startup.apply`) and
  errors as `type`, sanitized detail and `file.py:line function` frames. URLs, Windows/Unix paths and long tokens are
  replaced; no process output is stored. `update_log.describe(code)` gives the short Turkish reasons shown in the
  card (`error_message`, `apply_now.message`).
* **Stopping is all-or-loud.** `launcher.launch(stop=True)` (tray "Çık", `Orion.ps1 -Stop`) used to stop the Hub first
  and abort on its first exception, leaving the Router, tray, window and watcher untouched (a Hub shutdown
  timeout or a busy lock was enough). Now every component is attempted (a transient "busy" lock is retried twice),
  the tray and Orion's own window are always closed, the verified ownership state of Hub/Router is re-read
  (`running_services`) and any leftover or error raises `Orion tamamen kapatilamadi; calismaya devam eden: ...`
  (also in `updates/watcher.log`). `launcher_entry.ps1` shows that as a message box (`Show-OrionStopFailure`, plain
  text with `-NonInteractive`) instead of the misleading "could not start" repair dialog, and runs the launcher with
  `$ErrorActionPreference='Continue'` so native stderr can no longer abort the stop half way. A successful
  explicit stop also cancels a pending "update now" request, ends the updating window and asks the retained watcher
  to exit (`updates/watcher.exit`; the next launch clears it). The update flow's own stop passes `apply_owner=True`
  and does none of that.
* **Stale update requests.** `waiting_for_idle`/`requested` belong to the Hub that created them. A Hub start
  (`service_lifespan`) and an explicit stop `abandon` them (`cancelled`), and the watcher refuses a hand-over older
  than 90 s (`reconcile` first) instead of stopping a freshly started Hub; the card also ignores a
  `waiting_for_idle` request untouched for 60 s. The Router panel's "Güncellemeleri Denetle" writes
  `router-request.json`; on a Hub install the watcher answers it with one explicit hub-scope check that includes
  the Router (`check(manual=True, with_router=True)`, ignoring the 6 h spacing and the switches), and the Router
  status shows the outcome in Turkish (`check_outcome`, `message`), including "başka bir güncelleme hazır".
* **Watcher-path stop failure (root cause).** `services/shared/environment.py` runs `load_environment()` on import and
  resolved its repo root from its own file. In the retained watcher that is the *bootstrap bundle* (it has an
  `orion-release.json` but is not a registered component), so `runtime_layout.component()` raised "Package is not
  registered at this path" at the first `from services.shared.environment import get_env` inside `stop_hub` and every
  "Şimdi güncelle" stop failed, while `Orion.ps1 -Stop` (the Hub's own tree) worked. `load_environment` now returns
  quietly for an unregistered tree (the launcher supplies the registered component's environment).
  Test: `test_watcher_environment.py` runs the import in a subprocess with a bundle-like tree.
* **Own processes are not "uncoordinated".** `StoppedInstallation._unmanaged_consumers(wait=...)` retries the scan for
  up to `update_helper.CONSUMER_WAIT` (20 s) while the product's own processes settle (just started by the installer,
  or exiting after a stop); `StoppedPackages` then reports "not idle" (transaction stays `waiting_for_idle`) instead of
  raising `MigrationError`, and the watcher loop survives a process scan that cannot decide. Ownership that cannot be
  established (access denied) is not retried.
* **Download progress.** `ReleaseClient.prepare(..., progress=cb)` counts bytes through the updater's fetcher
  (`fetch` wrapped on the instance); `UpdateHelper.prepare` writes `progress = {done,total,component,bytes,bytes_total,
  rate}` into the journal about twice a second. The card shows "(2/2) · 85,3 / 198,0 MB · 1,4 MB/sn" and one bar for the
  whole download (bytes), polls every 2 s while checking/downloading, and says "Yeni sürüm bulundu" as soon as the
  check knows (`check_outcome: update_found` while `checking`). The updating window also shows the recorded result
  (`last-result.json`) before it closes.
* **Integrity levels.** An elevated start (administrator window, "Run as administrator") never starts services:
  `launcher_entry.ps1` (and `launcher.launch` as a second line) re-opens Orion with normal rights and exits:
  `explorer.exe Orion.exe` for a plain start (Explorer is the user's de-elevated shell), `runas /trustlevel:0x20000`
  for `-Startup/-Background`; `bootstrap/deelevate.stamp` stops a loop when that does not work (UAC off) and only then
  the Turkish message `ORION-ELEVATED-SELF` is shown. An access-denied lifecycle error (services owned by an elevated Orion) is printed as
  `ORION-ELEVATED:` and `launcher_entry.ps1` shows a Turkish message ("Orion yönetici olarak çalışıyor; kapatmak için
  tepsiden Çık, sonra normal açın") instead of the Repair dialog. `ORION_ALLOW_ELEVATED=1` is for sandboxes only.
* **One transaction, one restart.** For a Hub install the hub-scope check stages Hub, Client **and** the local Router
  (when the Router's switch is on, which follows the single "Otomatik güncellemeler" switch) in one
  `UpdateHelper.prepare`, so the user restarts once. Only components with a newer signed sequence join;
  compatibility is still checked over the whole set; a standalone Router keeps its own scheduler
  (`scope='router'`). If one of the packages fails to download or verify the whole transaction fails and nothing is
  switched (the next check retries); that is the price of a single restart, and the intended one.
* **Update screens.** While an update is applied (apply-now *and* the launch-time automatic apply) the app window is
  closed (`launcher.close_window`, only Orion's own profile), `services/shared/updating.ps1` (installed as
  `bootstrap/Orion-Updating.ps1`, WinForms, no dependencies, read-only) shows "Orion güncelleniyor, lütfen bekleyin
  (birkaç dakika sürebilir)." with the current step from the journal (`stopping`, `backing_up`, `switching`,
  `health_check`, `rollback_pending`, `restarting`), elapsed time and a "taking longer than expected" hint after 10
  idle minutes. `update_ui.updating_window()` wraps the whole apply and always closes it. Afterwards
  `updates/last-result.json` (`completed` / `rolled_back` / `failed` / `repair_required`, Turkish message, `seen`)
  is read by the Hub (`last_result`) and the reopened app window shows it once (`UpdateResultGate`;
  `POST /api/v1/updates/result/seen`, PC session only). `ORION_UPDATE_UI=off` (set by `scripts/release/conftest.py`)
  keeps sandboxes from opening windows. The card shows "Güncellemeler denetleniyor…" (`checking`: the check process
  records its pid; Hub adds a 20 s grace after the press) -> "Yeni sürüm bulundu, indiriliyor… (1/3)" (journal
  `progress`) -> "Hazır: Şimdi güncelle", or why nothing happened (`check_outcome`, `error_message`).
* **Product identity (`Orion.exe`)**: the desktop link and the Windows Startup entry used to target
  `powershell.exe`, so Task Manager's Startup apps and the desktop showed PowerShell's name and icon.
  `scripts/release/launcher/Orion.cs` (+ `orion.ico`, made from `installer/app/ui/favicon.png` by
  `launcher/make_icon.py`, Pillow only for regenerating) is a tiny `/target:winexe`: it starts
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File <own folder>\Orion.ps1` with
  only `-Startup/-Background/-Stop/-Repair/-NonInteractive` forwarded, without a console flash or waiting.
  `build_launcher.py` compiles it at release time with the .NET Framework `csc.exe` that ships with Windows
  (Title/Product "Orion AI Assistant", Company "Orion", version = bootstrap version); that compiler has no
  `/deterministic`, so the PE timestamp and module id are pinned and the same sources give the same bytes
  (the `bootstrap_gate` hash check keeps working). `build_bootstrap.py` puts `Orion.exe` in the bundle root and
  its sha256 in the bundle's `bootstrap.json` (`launcher_sha256`). `launcher.install_entry` copies it next to
  `bootstrap/Orion.ps1` only from the running/active retained bootstrap and only if the hash matches; nothing is
  downloaded or compiled on the user's machine. Links then point at `Orion.exe` (icon `Orion.exe,0`,
  description "Orion AI Assistant"). Startup entry name: `Orion AI Assistant.lnk` (desktop stays `Orion.lnk`).
  Without `Orion.exe` (source checkout, old bundle) the PowerShell form is kept as fallback.
  Existing installs: after the bootstrap update, the next `launcher.launch` runs `refresh_links` once per
  `Orion.exe` hash (`bootstrap/links.json`): it restyles an existing desktop link, replaces our old
  `Orion.lnk` in Startup by the new name only if Start with Windows is on, and only removes (never creates)
  it when the user turned it off. Foreign links are preserved; uninstall removes both names.
* **Tray icon** (`services/shared/tray.ps1`, installed as `bootstrap/Orion-Tray.ps1`): started by
  `launcher.launch` for installs with a Hub only; one icon per installation (named mutex), exits on
  `bootstrap/tray.exit` (written by `stop_tray` during every launcher stop; terminated only if it ignores it),
  or when the registry disappears. "Cik" hides the icon and runs `Orion.ps1 -Stop`.
* **Calm "updating" state:** `update_gate.progress()` reports a journal in an applying phase
  (`draining`..`rollback_pending`). `launcher.launch` and `launcher_entry.ps1` wait for it (progress window,
  30 min cap) and then continue; `repair_required`/corrupt journals stay a genuine Repair case.
* **No service worker in the client:** built with `--pwa-strategy=none`; `build_package.verify_no_service_worker`
  rejects anything else, and `serve_web.py` answers `/flutter_service_worker.js` with a self-removing worker so an
  already-registered old worker is retired on the next load. "Update now" reloads through `/__orion_refresh__`.

## One command: `release.cmd`

`release.cmd` (repo root) = `scripts/release/release.py`: build what changed (cache, parallel), full acceptance
(an identical earlier pass is reused), then sign and publish. The newest staged bootstrap bundle is reused while
`bootstrap.json` keeps its sequence. The key passphrase is asked once. `--no-publish` stops after acceptance,
`--dry-run` stages and signs without uploading, `--force-accept` repeats the acceptance.

## Two feeds: Hub/Client feed and the Router's own feed

Same key, same public root (`trusted-root.json`), two GitHub Release feeds (tag `feed` in each repository):

| Feed | Repository | Packages | Bootstrap bundle `release-source.json` |
| --- | --- | --- | --- |
| `--feed hub` (default) | `orion-ai-assistant/orion-ai-assistant` | Hub, Client | schema 2: Hub feed (`release-cache`) **and** the Router feed (`release-cache-router`) |
| `--feed router` | `orion-ai-assistant/orion-router` | Router | schema 2: the Router feed only (a Router-only install never points at the Hub feed) |

`build_release.py` is unchanged: it builds and accepts all three packages once into one candidate directory.
`publish_release.py --feed ...` publishes the subset that belongs to a feed from that directory; each feed counts
its own metadata `version`, has its own bootstrap bundle and its own `acceptance.json` (a subset of the accepted
set; a package that was not accepted is refused). `--repo`/`--tag` override the defaults; `--router-repo`,
`--router-tag`, `--no-router-source` only affect the Hub bundle's source list.

```powershell
# Order matters: the Router feed first, then the Hub feed (which no longer lists a Router target).
python scripts/release/publish_release.py --feed router --artifacts .tmp/candidates/<set> --accepted-result <result.json> --inputs .tmp/inputs
python scripts/release/publish_release.py --feed hub    --artifacts .tmp/candidates/<set> --accepted-result <result.json> --inputs .tmp/inputs
```

Both bundles are built from the same `bootstrap.json` (same `sequence`/`package_id`), so bump it once. The Router
feed's bootstrap asset (`orion-bootstrap-windows-x64.zip` in the Router repo's `feed` release) is what
`get-orion-router.ps1` downloads.

### `release-source.json`

* schema 1 (unchanged): one feed `{metadata_url, targets_url, targets}`; cache `release-cache`.
* schema 2: `{"schema": 2, "channel": "stable", "sources": [{metadata_url, targets_url, targets, cache?}, ...]}`
  (1-4 feeds, HTTPS only, no credentials/query; one component kind per feed; distinct URLs and cache names;
  `cache` is `release-cache` or `release-cache-<a-z0-9>`; default `release-cache-<hash of the URL>`).
* Every feed has its own `ReleaseClient` and TUF cache (`<Orion>\release-cache*`), so metadata versions and
  rollback protection are per feed. `AutomaticUpdates.check`, the watcher, refresh, "Şimdi güncelle" and the
  bootstrap self-update walk all feeds, merge what is newer into **one** transaction (`compatible()` runs over
  the merged set and the installed sequences; one restart) and tolerate a failed or unreachable feed
  (`automatic.json` `feed_errors`; only "every feed failed" is a failed check).
* The running bootstrap defines the feeds: at the start of a check/watch it merges its bundle's
  `release-source.json` into `bootstrap/release-source.json` (union by feed URL, the bundle wins for the components
  it lists). That is how an existing Hub install learns the Router feed after the bootstrap self-updates.
  A newer bundle from any feed is adopted (highest signed sequence).

## Install, add a component, uninstall

* `Install-Orion.ps1 -Components hub client router [-AddMissing]` (inside the bundle) installs from the bundle's
  `release-source.json` (`install_new.py --release-source ... --components ...`). With no registry it is a normal
  first install; **with** a registry it takes the *add* path (`services/shared/component_setup.py`).
* Add: only components that are not registered are installed (Hub+Client next to a Router-only install, a Router
  next to a Hub install); registered ones keep their registry entry, code, data, identity and releases. Under the
  operation lock with `updates/add-journal.json` (previous registry + folders created); any failure or crash rolls
  the registry back and deletes only the folders the journal created (`recover_incomplete_add` runs first at the
  next add). Refused with a Turkish message on: already installed, pending/blocked update, foreign trust root,
  incompatible signed versions, occupied folder, source/development registrations.
* Router-only installs have their own identity: desktop/Startup link `Orion Router.lnk`, launcher
  `OrionRouter.exe` (file description "Orion Router"), Settings > Apps entry "Orion Router". Adding Hub swaps the
  links/exe/entry to "Orion AI Assistant" in place.
* Uninstall: Settings > Apps > Orion AI Assistant > Uninstall (HKCU `...\Uninstall\OrionAIAssistant`, script
  `bootstrap\Orion-Uninstall.ps1`) or the tray's "Orion'u kaldır": Hub only / Router only / everything, keep (default)
  or delete data and models. `services/shared/uninstall.py` builds on `package_maintenance` (plan, expected-revision
  uninstall, bounded code purge). Kept data gets a marker so a later install adopts it (same secrets/identity).
  Never touched: `hub-installation.json`, `router-installation.json`, `lifecycle-tests`, anything outside the
  installation root. Firewall (UAC once; declined/failed never fails the uninstall): the rule shrinks to what
  remains or is removed with the last component.
* Startup list: `Task Manager > Startup` names an entry after the target exe's file description. Explorer keeps one
  `StartupApproved\StartupFolder` value per link *name* and never deletes it; a stale `Orion.lnk` value from older
  installs lingered and listed "Orion.exe". Install/update (`refresh_links`) and uninstall now delete Orion's
  orphaned values (`windows_autostart.clean_startup_approved`), foreign values stay.

`release.cmd --no-accept` skips the automated package acceptance for fast developer iterations: the exact package
set is still signed and bound (`acceptance.json` with `acceptance_skipped: true`), but nothing proved that it installs
and starts. Use the default (with acceptance) for releases users depend on.
