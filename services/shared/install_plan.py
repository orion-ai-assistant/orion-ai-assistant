"""What an installation type installs: the one table the setup window and the silent install both read.

Two types:
    merkez  this PC becomes the Orion center: Hub, the Orion window and the Router (its database, Redis and
            worker come with them)
    baglan  this PC only uses another PC's Hub, like a phone: the Orion window and a light Hub that relays
            it (ORION_HUB_MODE=relay; no database, Redis, Router or phone listener; its firewall rule only
            lets it find the center on the network)

A new component is one row here. `required` rows are installed without asking; optional ones are the
setup window's check boxes.
"""
from dataclasses import dataclass, field

MODES = ('merkez', 'baglan')


@dataclass(frozen=True)
class Component:
    kind: str
    title: str
    detail: str
    modes: tuple
    required: bool = True
    settings: dict = field(default_factory=dict)


COMPONENTS = (
    Component('hub', 'Orion Hub', 'Hesaplar, sohbetler, telefon bağlantısı ve veritabanı', ('merkez',)),
    Component('hub', 'Bağlantı servisi', 'Bu bilgisayarı diğer bilgisayardaki Hub’a bağlar (hafif Hub)',
              ('baglan',), settings={'ORION_HUB_MODE': 'relay'}),
    Component('client', 'Orion penceresi', 'Sohbet ve ayarlar ekranı', MODES),
    Component('router', 'Orion Router', 'Yapay zekâ sağlayıcılarına bağlanır', ('merkez',)),
)


def components(mode):
    if mode not in MODES:
        raise ValueError('Kurulum türü merkez ya da baglan olmalı.')
    return [row for row in COMPONENTS if mode in row.modes]


def settings(mode):
    """Initial component settings (install_new --settings) for this installation type."""
    return {row.kind: dict(row.settings) for row in components(mode) if row.settings}


def plan(mode, record=None, *, hub_mode=None):
    """Each row with what happens to it: 'install', or 'present' when the installation already has it.

    `record` is the existing registry (None for a new installation). An existing Hub of the other type is a
    'conflict': the types differ in what the Hub keeps, so the setup window offers no silent change."""
    present = {c['kind']: c for c in (record or {}).get('components', [])}
    rows = []
    for row in components(mode):
        action = 'present' if row.kind in present else 'install'
        if row.kind == 'hub' and row.kind in present and hub_mode is not None:
            wanted = 'relay' if row.settings.get('ORION_HUB_MODE') == 'relay' else 'full'
            if hub_mode(present['hub']['code_path']) != wanted:
                action = 'conflict'
        rows.append({'kind': row.kind, 'title': row.title, 'detail': row.detail, 'required': row.required,
                     'action': action})
    return rows


def firewall(mode):
    """Both types ask Windows once, at install (one UAC prompt): the center listens for phones, other PCs and
    the Router; a light Hub listens for the center's announcement ("Cihazları ara"). Without the rule Windows
    would ask "allow Python?" later, in the middle of using Orion."""
    return mode in MODES


def firewall_note(mode):
    """What the one UAC prompt is for, in the user's words (setup window and silent install)."""
    if mode == 'baglan':
        return ('Windows bir kez yönetici izni soracak (Windows PowerShell): bu bilgisayarın ağdaki Orion '
                'merkezini bulabilmesi için. "Evet" de; Windows sonra ayrıca bir şey sormaz.')
    return ('Windows bir kez yönetici izni soracak (Windows PowerShell): telefonların ve diğer bilgisayarların '
            'bu Orion’a bağlanabilmesi için. "Evet" de; Windows sonra ayrıca bir şey sormaz.')
