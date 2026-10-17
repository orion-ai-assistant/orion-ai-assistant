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
--release --no-wasm-dry-run`; build Router with `npm ci` and `npm run build` first.
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

17 October user authorization adds Stage 06 only. Flutter remains web; no native
Flutter Windows EXE or public production installer is claimed. See
[manual web trial](../../docs/orion-lifecycle-plan/06-manual-test.md).

Retain the trusted installer ZIP separately from Hub releases. A newly built
bootstrap contains `scripts/release/update_local.py`, the helper and its bundled
Python/dependencies. Extract a new bootstrap alongside the old one; never replace
the only running recovery copy. Trust-root changes still require TUF rotation;
this is not an unsigned bootstrap self-update channel.

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
quarantine during health checks.

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
