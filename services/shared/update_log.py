"""Private, size-limited diagnostics for the update watcher and its helper processes.

The watcher and the check/startup processes run without a console (stdout/stderr are discarded), so
a failure used to leave only an error class in a state file. This keeps one short line per event in
`updates/watcher.log` (rotated, a few hundred KiB at most). Nothing sensitive is written: URLs,
file-system paths, long tokens and process output are replaced before a line is stored, and
tracebacks keep only `module.py:line function` frames. Logging never raises.
"""
import os
from pathlib import Path
import re
import time
import traceback

LOG_NAME = 'watcher.log'
MAX_BYTES = 256 * 1024
BACKUPS = 2
MAX_FIELD = 240

_URL = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://\S+')
_PATH = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|tmp|var|usr|opt)/)[^\s\'"<>|]*')
_TOKEN = re.compile(r'\b[A-Za-z0-9_\-]{32,}\b')


def sanitize(value):
    text = ' '.join(str(value).split())
    text = _URL.sub('<url>', text)
    text = _PATH.sub('<path>', text)
    text = _TOKEN.sub('<redacted>', text)
    return text[:MAX_FIELD]


def _file(base):
    return Path(base) / LOG_NAME


def _rotate(path):
    try:
        if path.stat().st_size < MAX_BYTES:
            return
    except OSError:
        return
    for index in range(BACKUPS, 0, -1):
        source = path if index == 1 else path.with_name(f'{LOG_NAME}.{index - 1}')
        try:
            if source.exists():
                os.replace(source, path.with_name(f'{LOG_NAME}.{index}'))
        except OSError:
            pass


def log(base, event, **fields):
    """Append one line. `base` is the install's updates folder."""
    try:
        path = _file(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        parts = [time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime()), f'pid={os.getpid()}', sanitize(event)]
        parts += [f'{key}={sanitize(value)}' for key, value in fields.items() if value is not None]
        with path.open('a', encoding='utf-8') as stream:
            stream.write(' '.join(parts) + '\n')
    except Exception:
        pass  # Diagnostics only.


def frames(error, limit=6):
    """`file.py:line function` of the innermost frames: enough to find the failing call."""
    try:
        return ' <- '.join(f'{Path(f.filename).name}:{f.lineno} {f.name}'
                           for f in reversed(traceback.extract_tb(error.__traceback__)[-limit:]))
    except Exception:
        return None


def log_exception(base, where, error):
    log(base, 'error', where=where, type=type(error).__name__, detail=sanitize(error) or None,
        at=frames(error))


def read_tail(base, lines=40):
    try:
        return _file(base).read_text(encoding='utf-8').splitlines()[-lines:]
    except OSError:
        return []


# Short Turkish reasons for the failure codes users can meet (never raw exception text).
REASONS = {
    'BootstrapIncompatible': 'Güncelleme yardımcısı bu işlemi tanımıyor (eski sürüm). Orion’u yeniden başlatın; yardımcı kendini güncelleyecektir.',
    'CheckProcessFailed': 'Denetim işlemi başlatılamadı veya hata ile kapandı. Ayrıntı updates/watcher.log dosyasında.',
    'CheckProcessNotStarted': 'Güncelleme denetimi başlatılamadı (güncelleme yardımcısı çalıştırılamadı).',
    'LifecycleError': 'Başka bir Orion işlemi kurulumu kullanıyor; biraz sonra yeniden denenecek.',
    'OperationBusy': 'Başka bir Orion işlemi kurulumu kullanıyor; hiçbir şey durdurulmadı. Biraz sonra yeniden deneyin.',
    'URLError': 'Güncelleme sunucusuna ulaşılamadı. İnternet bağlantısını kontrol edin.',
    'TimeoutError': 'Güncelleme sunucusu zamanında yanıt vermedi.',
    'ConnectionError': 'Güncelleme sunucusuna ulaşılamadı. İnternet bağlantısını kontrol edin.',
    'RepositoryError': 'İmzalı güncelleme bilgisi doğrulanamadı.',
    'PackageError': 'İndirilen paket doğrulanamadı; güncelleme uygulanmadı.',
    'UpdateError': 'Güncelleme hazırlanamadı (imza, uyumluluk veya bekleyen işlem denetimi). Ayrıntı updates/watcher.log dosyasında.',
    'CheckUnavailable': 'Güncelleme bilgisi şu an alınamıyor.',
    'StagedUpdateChanged': 'Hazır güncelleme istekten sonra değişti; güncelleme uygulanmadı.',
    'ServicesStillRunning': 'Servisler kapatılamadı; güncelleme uygulanmadı, önceki sürümle devam ediliyor.',
    'RequestLost': 'Güncelleme isteği yarıda kaldı. Yeniden deneyin.',
    'WatcherDidNotRespond': 'Güncelleme yardımcısı isteği almadı. Orion’u yeniden başlatıp deneyin.',
    'Stalled': 'Güncelleme beklenenden uzun sürdü ve durduruldu. Orion önceki sürümle açıldı.',
    'ActivityCheckFailed': 'Orion meşgul olup olmadığını belirleyemedi; güncelleme uygulanmadı.',
    'MigrationError': 'Orion süreçleri güncelleme sırasında hâlâ çalışıyordu; güncelleme uygulanmadı.',
    'RuntimeError': 'Güncelleme uygulanamadı (beklenmeyen hata). Ayrıntı updates/watcher.log dosyasında.',
    'Candidate health failed': 'Yeni sürüm sağlık denetiminden geçemedi; önceki sürüme dönüldü.',
    'CandidateHealthFailed': 'Yeni sürüm sağlık denetiminden geçemedi; önceki sürüme dönüldü.',
    'UpdateNotApplied': 'Güncelleme uygulanamadı; önceki sürümle devam ediliyor.',
    'rolled_back': 'Yeni sürüm çalışmadı; önceki sürüme dönüldü.',
    'repair_required': 'Güncelleme yarıda kaldı; yerel onarma gerekli.',
    'failed': 'Güncelleme başarısız oldu; önceki sürümle devam ediliyor.',
    'SourceUnrecognized': 'Güncelleme kaynağı tanınmadı (kaynak dosyası bu sürümün okuyamadığı biçimde). Güncelleme yardımcısı dosyayı düzeltecek; Orion’u yeniden başlatın.',
    'PolicyUnrecognized': 'Güncelleme ayarları dosyası tanınmadı; varsayılan ayarlar gösteriliyor.',
}


def describe(code):
    """Short Turkish text for an error code; unknown codes get a generic line plus the class name."""
    if not code:
        return None
    code = str(code)
    if code in REASONS:
        return REASONS[code]
    if code.startswith('Candidate health') or 'health' in code.lower():
        return REASONS['Candidate health failed']
    safe = sanitize(code)[:60]
    return f'Güncelleme tamamlanamadı ({safe}). Ayrıntı updates/watcher.log dosyasında.'
