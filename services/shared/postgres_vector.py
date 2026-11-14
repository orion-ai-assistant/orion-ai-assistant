"""pgvector (the `vector` extension) in the Hub's own PostgreSQL: embeddings for the memory.

pgvector publishes no Windows binaries. scripts/release/build_pgvector.py builds them once with MSVC from the
pinned source against the pinned PostgreSQL archive (windows_postgres.py) into one small zip kept in git
(ARCHIVE). The Hub's runtime layer (build_package.py) and a developer's .local_db (local_db_setup.py) unpack it
into the runtime's lib/ and share/extension/.

vector.control is not `trusted`: only a superuser may create the extension. The Hub's supervisor (run_local.py)
does it with the admin role of postgres_auth.py once the server is up; the app role then uses the type, its
operators and the HNSW/IVFFlat index methods in its own tables. Files missing or the statement failing never
stop the Hub: it says so and runs without vectors."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import zipfile

VERSION = '0.8.7'
ARCHIVE = (Path(__file__).resolve().parents[2] / 'scripts/release/pgvector'
           / f'pgvector-{VERSION}-pg16-windows-x64.zip')
ARCHIVE_SHA256 = '6b36eed9ea24a9447d8d61007c4b7bea5a4c8555a9f51e50226743bdca5414b8'
FILES = ('lib/vector.dll', 'share/extension/vector.control', f'share/extension/vector--{VERSION}.sql',
         'pgvector-license.txt')
CONTROL = 'share/extension/vector.control'

# The extension at the version its files carry. ALTER only when the installed one is older (a later pgvector
# zip), so a usual start runs one CREATE ... IF NOT EXISTS and nothing else.
ENABLE = (
    'CREATE EXTENSION IF NOT EXISTS vector;\n'
    "SELECT 'ALTER EXTENSION vector UPDATE' FROM pg_extension e JOIN pg_available_extensions a "
    "ON a.name = e.extname WHERE e.extname = 'vector' AND e.extversion IS DISTINCT FROM a.default_version \\gexec\n")


def _sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_of(pg):
    """The runtime folder (bin/, lib/, share/) of a native_postgres.Postgres."""
    return Path(pg.ctl).parent.parent


def present(runtime):
    return (Path(runtime) / CONTROL).is_file() and (os.name != 'nt' or (Path(runtime) / FILES[0]).is_file())


def install(runtime, archive=ARCHIVE, expected=ARCHIVE_SHA256):
    """Unpack the pinned pgvector files into a PostgreSQL runtime. Files already there with the same bytes stay;
    each one is written beside its place and renamed, so a running server never loads half a DLL."""
    runtime, archive = Path(runtime), Path(archive)
    if _sha256(archive) != expected:
        raise ValueError('pgvector archive SHA256 mismatch; nothing installed')
    written = []
    with zipfile.ZipFile(archive) as source:
        if sorted(source.namelist()) != sorted(FILES):
            raise ValueError('Unexpected pgvector archive contents')
        for name in FILES:
            data = source.read(name)
            target = runtime / PurePosixPath(name)
            if target.is_file() and target.read_bytes() == data:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.orion-tmp')
            temporary.write_bytes(data)
            os.replace(temporary, target)
            written.append(name)
    return written


def enable(pg, admin, database, report=print):
    """CREATE EXTENSION vector in `database` as the superuser `admin` (name, password). True when it is there.

    Never raises: whatever goes wrong is reported and the Hub starts without vectors."""
    try:
        runtime = runtime_of(pg)
        if not present(runtime):
            report(f'[!] pgvector files are missing in {runtime}; the Hub runs without vector search', flush=True)
            return False
        from services.shared.postgres_auth import _psql
        _psql(pg, admin, database, ENABLE)
    except Exception as error:
        report(f'[!] pgvector could not be enabled; the Hub runs without vector search: {error}', flush=True)
        return False
    return True
