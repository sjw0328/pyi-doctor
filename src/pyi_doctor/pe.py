"""Minimal, read-only PE (Windows executable) reader.

Only what the antivirus diagnosis needs: section names, presence of version /
icon / manifest resources, Authenticode signature directory, the overlay and
the PyInstaller archive cookie. Pure standard library; works on any OS.
"""

from __future__ import annotations

import mmap
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Tuple

RT_ICON_GROUP = 14
RT_VERSION = 16
RT_MANIFEST = 24
PYINSTALLER_COOKIE = b"MEI\014\013\012\013\016"
UPX_SECTION_NAMES = ("UPX0", "UPX1", "UPX2", ".UPX0", ".UPX1", "UPX!")

MACHINES = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0x1C4: "arm"}


class NotAPEFile(ValueError):
    """The file is not a valid PE executable."""


@dataclass
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_pointer: int
    raw_size: int


@dataclass
class PEInfo:
    path: str
    size: int
    machine: str
    is_64bit: bool
    sections: List[str] = field(default_factory=list)
    resource_types: List[int] = field(default_factory=list)
    signed: bool = False
    signature_size: int = 0
    overlay_size: int = 0
    pyinstaller_archive: bool = False

    @property
    def has_version_info(self) -> bool:
        return RT_VERSION in self.resource_types

    @property
    def has_icon(self) -> bool:
        return RT_ICON_GROUP in self.resource_types

    @property
    def has_manifest(self) -> bool:
        return RT_MANIFEST in self.resource_types

    @property
    def upx_packed(self) -> bool:
        return any(s.upper() in UPX_SECTION_NAMES for s in self.sections)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            has_version_info=self.has_version_info,
            has_icon=self.has_icon,
            has_manifest=self.has_manifest,
            upx_packed=self.upx_packed,
        )
        return d


def _u16(b, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _u32(b, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def parse(path) -> PEInfo:
    """Parse ``path`` and return :class:`PEInfo`. Raises :class:`NotAPEFile`."""
    p = Path(path)
    size = p.stat().st_size
    if size < 0x40:
        raise NotAPEFile(f"{p}: too small to be an executable")
    with open(p, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
        try:
            return _parse(data, str(p), size)
        except (struct.error, IndexError) as exc:
            raise NotAPEFile(f"{p}: truncated or malformed PE ({exc})") from exc


def _parse(data, path: str, size: int) -> PEInfo:
    if data[:2] != b"MZ":
        raise NotAPEFile(f"{path}: missing MZ header")
    e_lfanew = _u32(data, 0x3C)
    if e_lfanew + 24 > size or data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise NotAPEFile(f"{path}: missing PE signature")
    coff = e_lfanew + 4
    machine = _u16(data, coff)
    n_sections = _u16(data, coff + 2)
    opt_size = _u16(data, coff + 16)
    opt = coff + 20
    magic = _u16(data, opt)
    if magic == 0x20B:
        is64, dirs_count_off = True, 108
    elif magic == 0x10B:
        is64, dirs_count_off = False, 92
    else:
        raise NotAPEFile(f"{path}: unknown optional header magic 0x{magic:x}")
    n_dirs = _u32(data, opt + dirs_count_off)
    dirs_off = opt + dirs_count_off + 4

    def directory(index: int) -> Tuple[int, int]:
        if index >= n_dirs:
            return 0, 0
        return _u32(data, dirs_off + index * 8), _u32(data, dirs_off + index * 8 + 4)

    sections: List[Section] = []
    sec_off = opt + opt_size
    for i in range(n_sections):
        o = sec_off + i * 40
        raw_name = bytes(data[o : o + 8]).rstrip(b"\0")
        sections.append(
            Section(
                name=raw_name.decode("latin-1"),
                virtual_size=_u32(data, o + 8),
                virtual_address=_u32(data, o + 12),
                raw_size=_u32(data, o + 16),
                raw_pointer=_u32(data, o + 20),
            )
        )

    def rva_to_offset(rva: int) -> int:
        for s in sections:
            span = max(s.virtual_size, s.raw_size)
            if s.virtual_address <= rva < s.virtual_address + span:
                return s.raw_pointer + (rva - s.virtual_address)
        return -1

    # Resource types (root level of the resource directory only).
    resource_types: List[int] = []
    res_rva, res_size = directory(2)
    if res_rva and res_size:
        base = rva_to_offset(res_rva)
        if 0 <= base and base + 16 <= size:
            n_named = _u16(data, base + 12)
            n_ids = _u16(data, base + 14)
            for i in range(n_named, n_named + n_ids):
                ent = base + 16 + i * 8
                if ent + 8 > size:
                    break
                name_field = _u32(data, ent)
                if not name_field & 0x80000000:
                    resource_types.append(name_field & 0xFFFF)

    # Authenticode: the security directory holds a FILE offset, not an RVA.
    sig_off, sig_size = directory(4)
    signed = bool(sig_off and sig_size and sig_off + sig_size <= size)

    image_end = max((s.raw_pointer + s.raw_size for s in sections if s.raw_size), default=0)
    overlay = max(0, size - image_end)
    archive = False
    if overlay:
        search_end = sig_off if signed and sig_off > image_end else size
        window_start = max(image_end, search_end - 4096)
        archive = data.rfind(PYINSTALLER_COOKIE, window_start, search_end) != -1

    return PEInfo(
        path=path,
        size=size,
        machine=MACHINES.get(machine, hex(machine)),
        is_64bit=is64,
        sections=[s.name for s in sections],
        resource_types=sorted(set(resource_types)),
        signed=signed,
        signature_size=sig_size if signed else 0,
        overlay_size=overlay,
        pyinstaller_archive=archive,
    )
