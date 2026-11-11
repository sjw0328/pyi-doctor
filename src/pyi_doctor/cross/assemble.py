"""Write PyInstaller's archive formats and the final Windows executable.

The formats follow PyInstaller's own writers (``PyInstaller.archive.writers``)
for the installed PyInstaller version; they are re-implemented here only to
use Windows path separators and code objects that were found without
importing anything.
"""

from __future__ import annotations

import importlib.util
import io
import marshal
import os
import shutil
import struct
import sys
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from types import CodeType
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .analysis import Collected, PureModule

_COOKIE_MAGIC = b"MEI\014\013\012\013\016"
_COOKIE_FORMAT = "!8sIIII64s"
_TOC_ENTRY_FORMAT = "!IIIIBc"
_TOC_ENTRY_LEN = struct.calcsize(_TOC_ENTRY_FORMAT)
_PYZ_MAGIC = b"PYZ\0"
_PYZ_HEADER_LEN = 12 + 5
BYTECODE_MAGIC = importlib.util.MAGIC_NUMBER
CONTENTS_DIR = "_internal"


def _replace_filename(code: CodeType, filename: str) -> CodeType:
    from PyInstaller.building.utils import replace_filename_in_code_object

    return replace_filename_in_code_object(code, filename)


def _win(path: str) -> str:
    return str(PurePosixPath(path)).replace("/", "\\")


# --------------------------------------------------------------------- PYZ
def write_pyz(path: Path, modules: Iterable[PureModule]) -> Path:
    from PyInstaller.loader.pyimod01_archive import PYZ_ITEM_MODULE, PYZ_ITEM_NSPKG, PYZ_ITEM_PKG

    entries = sorted(modules, key=lambda m: m.name)
    with open(path, "wb") as fp:
        fp.write(b"\0" * _PYZ_HEADER_LEN)
        toc = []
        for m in entries:
            if m.code is None:
                toc.append((m.name, (PYZ_ITEM_NSPKG, fp.tell(), 0)))
                continue
            parts = m.name.split(".")
            if m.is_package:
                typ, co_filename = PYZ_ITEM_PKG, "\\".join(parts + ["__init__.py"])
            else:
                typ, co_filename = PYZ_ITEM_MODULE, "\\".join(parts) + ".py"
            blob = zlib.compress(marshal.dumps(_replace_filename(m.code, co_filename)), 6)
            toc.append((m.name, (typ, fp.tell(), len(blob))))
            fp.write(blob)
        toc_offset = fp.tell()
        fp.write(marshal.dumps(toc))
        fp.seek(0)
        fp.write(_PYZ_MAGIC + BYTECODE_MAGIC + struct.pack("!i", toc_offset))
    return path


def write_base_library(path: Path, modules: Iterable[PureModule]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for m in sorted(modules, key=lambda m: m.name):
            if m.code is None:
                continue
            dest = "/".join(m.name.split(".")) + ("/__init__.pyc" if m.is_package else ".pyc")
            code = _replace_filename(m.code, dest[:-1].replace("/", "\\"))
            buf = io.BytesIO()
            buf.write(BYTECODE_MAGIC)
            buf.write(struct.pack("<I", 0b01))  # hash-based pyc, check_source=False
            buf.write(b"\0" * 8)
            marshal.dump(code, buf)
            zf.writestr(zipfile.ZipInfo(dest), buf.getvalue())
    return path


# ---------------------------------------------------------------- CArchive
class _CArchive:
    def __init__(self, fp):
        self.fp = fp
        self.toc: List[Tuple[int, int, int, int, str, str]] = []

    def blob(self, name: str, data: bytes, typecode: str, compress: bool) -> None:
        off = self.fp.tell()
        raw_len = len(data)
        if compress:
            data = zlib.compress(data, 9)
        self.fp.write(data)
        self.toc.append((off, len(data), raw_len, int(compress), typecode, name))

    def file(self, name: str, src: Path, typecode: str, compress: bool) -> None:
        off = self.fp.tell()
        raw_len = src.stat().st_size
        with open(src, "rb") as fh:
            if compress:
                comp = zlib.compressobj(9)
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    self.fp.write(comp.compress(chunk))
                self.fp.write(comp.flush())
            else:
                shutil.copyfileobj(fh, self.fp)
        self.toc.append((off, self.fp.tell() - off, raw_len, int(compress), typecode, name))

    def finish(self, start: int, pylib_name: str) -> None:
        parts = []
        for off, clen, dlen, comp, typ, name in self.toc:
            raw = name.encode("utf-8")
            nlen = len(raw) + 1
            if (_TOC_ENTRY_LEN + nlen) % 16:
                nlen += 16 - (_TOC_ENTRY_LEN + nlen) % 16
            parts.append(struct.pack(_TOC_ENTRY_FORMAT + f"{nlen}s", _TOC_ENTRY_LEN + nlen, off - start, clen, dlen,
                                     comp, typ.encode("ascii"), raw))
        toc = b"".join(parts)
        toc_offset = self.fp.tell() - start
        self.fp.write(toc)
        length = toc_offset + len(toc) + struct.calcsize(_COOKIE_FORMAT)
        pyvers = sys.version_info[0] * 100 + sys.version_info[1]
        self.fp.write(struct.pack(_COOKIE_FORMAT, _COOKIE_MAGIC, length, toc_offset, len(toc), pyvers,
                                  pylib_name.encode("ascii")))


def bootstrap_modules(pyi_dir: Path, collected: Collected) -> List[Tuple[str, CodeType]]:
    """PyInstaller's loader modules (``m`` entries), compiled from the Windows wheel's sources."""
    out: List[Tuple[str, CodeType]] = []
    struct_mod = collected.pure.get("struct")
    if struct_mod is None or struct_mod.code is None:
        raise RuntimeError("stdlib module 'struct' was not collected")
    out.append(("struct", struct_mod.code))
    for name in ("pyimod01_archive", "pyimod02_importers", "pyimod03_ctypes", "pyimod04_pywin32"):
        src = pyi_dir / "loader" / f"{name}.py"
        out.append((name, compile(src.read_bytes(), str(src), "exec", dont_inherit=True, optimize=0)))
    return out


BOOTSTRAP_NAMES = {"struct", "pyimod01_archive", "pyimod02_importers", "pyimod03_ctypes", "pyimod04_pywin32"}


def write_exe(
    exe_path: Path,
    *,
    bootloader_image: bytes,
    pyi_dir: Path,
    collected: Collected,
    pyz: Path,
    onefile: bool,
    base_library: Optional[Path],
    options: Sequence[str] = (),
) -> Path:
    """Write ``bootloader_image`` (already carrying resources) followed by the PKG archive."""
    pylib = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    with open(exe_path, "wb") as fp:
        fp.write(bootloader_image)
        start = fp.tell()
        arc = _CArchive(fp)
        for name, code in bootstrap_modules(pyi_dir, collected):
            arc.blob(name, marshal.dumps(_replace_filename(code, name + ".py")), "m", True)
        boot_src = pyi_dir / "loader" / "pyiboot01_bootstrap.py"
        boot = compile(boot_src.read_bytes(), str(boot_src), "exec", dont_inherit=True, optimize=0)
        arc.blob("pyiboot01_bootstrap", marshal.dumps(_replace_filename(boot, "pyiboot01_bootstrap.py")), "s", True)
        for name, code in collected.scripts:
            arc.blob(name, marshal.dumps(_replace_filename(code, name + ".py")), "s", True)
        rest: List[Tuple[str, str, object]] = [(opt, "o", None) for opt in options]
        if onefile:
            for dest, src in collected.binaries.items():
                rest.append((_win(dest), "b", src))
            for dest, src in collected.datas.items():
                rest.append((_win(dest), "x", src))
            if base_library is not None:
                rest.append(("base_library.zip", "x", base_library))
        rest.append(("PYZ.pyz", "z", pyz))
        rest.sort(key=lambda e: (e[1], e[0]))
        for name, typ, src in rest:
            if typ == "o":
                arc.blob(name, b"", "o", False)
            else:
                arc.file(name, Path(src), typ, compress=(typ != "z"))
        arc.finish(start, pylib)
    return exe_path


def write_onedir_files(folder: Path, collected: Collected, base_library: Path) -> None:
    internal = folder / CONTENTS_DIR
    internal.mkdir(parents=True, exist_ok=True)
    seen: Dict[str, str] = {}
    for table in (collected.binaries, collected.datas):
        for dest, src in table.items():
            key = dest.lower()
            if key in seen:
                continue  # Windows file names are case-insensitive
            seen[key] = dest
            target = internal / PurePosixPath(dest)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    shutil.copyfile(base_library, internal / "base_library.zip")
