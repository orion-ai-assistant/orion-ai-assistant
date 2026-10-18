"""Compile Orion.exe with the .NET Framework compiler that ships with Windows (no SDK, no downloads)."""
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

SOURCE = Path(__file__).with_name('launcher')


def compiler():
    root = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'Microsoft.NET'
    for flavour in ('Framework64', 'Framework'):
        csc = root / flavour / 'v4.0.30319/csc.exe'
        if csc.is_file():
            return csc
    raise RuntimeError('The .NET Framework compiler (csc.exe) was not found; build Orion.exe on Windows')


def make_reproducible(path, seed):
    """The Framework compiler has no /deterministic: pin its two random bytes (PE timestamp, module id).

    The same sources then give the same Orion.exe, so a Hub-only release that rebuilds the bootstrap
    keeps the already published hash (see publish_release.bootstrap_gate).
    """
    data = bytearray(Path(path).read_bytes())
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Orion.exe is not a PE file')
    data[pe + 8:pe + 12] = b'\0\0\0\0'
    sections, optional = struct.unpack_from('<H', data, pe + 6)[0], struct.unpack_from('<H', data, pe + 20)[0]
    magic = struct.unpack_from('<H', data, pe + 24)[0]
    directories = pe + 24 + (96 if magic == 0x10B else 112)
    clr_rva = struct.unpack_from('<I', data, directories + 14 * 8)[0]
    table = pe + 24 + optional

    def offset(rva):
        for index in range(sections):
            name, size, address, raw_size, raw = struct.unpack_from('<8sIIII', data, table + index * 40)
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError('Orion.exe has no managed metadata')

    metadata = offset(struct.unpack_from('<I', data, offset(clr_rva) + 8)[0])
    length = struct.unpack_from('<I', data, metadata + 12)[0]
    count = struct.unpack_from('<H', data, metadata + 16 + length + 2)[0]
    position = metadata + 16 + length + 4
    for _ in range(count):
        start, size = struct.unpack_from('<II', data, position)
        end = position + 8
        while data[end]:
            end += 1
        if bytes(data[position + 8:end]) == b'#GUID':
            data[metadata + start:metadata + start + 16] = hashlib.sha256(seed).digest()[:16]
            Path(path).write_bytes(bytes(data))
            return
        position = metadata + ((end - metadata + 4) & ~3)
    raise ValueError('Orion.exe has no module id')


def build_launcher(output, version, product='Orion AI Assistant'):
    """`version` is the bootstrap version (x.y.z); it becomes the exe's file/product version.

    `product` is the exe's file description, which Task Manager > Startup shows ("Orion Router" for a Router-only install)."""
    if product not in {'Orion AI Assistant', 'Orion Router'}:
        raise ValueError('Unknown launcher product')
    if not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', version):
        raise ValueError('Launcher version must be x.y.z')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    info = (f'using System.Reflection;\n'
            f'[assembly: AssemblyTitle("{product}")]\n'
            f'[assembly: AssemblyDescription("{product}")]\n'
            f'[assembly: AssemblyProduct("{product}")]\n'
            f'[assembly: AssemblyCompany("Orion")]\n'
            f'[assembly: AssemblyCopyright("Orion")]\n'
            f'[assembly: AssemblyVersion("{version}.0")]\n'
            f'[assembly: AssemblyFileVersion("{version}.0")]\n')
    with tempfile.TemporaryDirectory(prefix='orion-launcher-') as temporary:
        info_file = Path(temporary) / 'AssemblyInfo.cs'
        info_file.write_text(info, encoding='ascii')
        subprocess.run([str(compiler()), '/nologo', '/target:winexe', '/optimize+', '/warnaserror+',
                        f'/out:{output}', f'/win32icon:{SOURCE / "orion.ico"}',
                        str(SOURCE / 'Orion.cs'), str(info_file)],
                       check=True, capture_output=True, timeout=120, creationflags=0x08000000)
    make_reproducible(output, b'orion-launcher' + product.encode() + (SOURCE / 'Orion.cs').read_bytes() + (SOURCE / 'orion.ico').read_bytes() + version.encode())
    return output
