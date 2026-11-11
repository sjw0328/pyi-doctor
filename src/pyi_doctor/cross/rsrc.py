"""Write Windows PE resources (icon, version info, manifest) without Windows.

The PyInstaller bootloader ships without a ``.rsrc`` section. On Windows,
PyInstaller adds one through the ``UpdateResource`` API. Here the section is
built in pure Python and inserted before ``.reloc`` (which is moved to the
end of the image), so the result has the usual MSVC section order.

Only 64-bit (PE32+) images without an existing resource section are
handled; anything else raises :class:`RsrcError`.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

RT_ICON = 3
RT_GROUP_ICON = 14
RT_VERSION = 16
RT_MANIFEST = 24
LANG_NEUTRAL = 0

_SCN_RSRC = 0x40000040  # INITIALIZED_DATA | MEM_READ
_SCN_INIT_DATA = 0x00000040
_DIR_RESOURCE = 2
_DIR_SECURITY = 4
_DIR_BASERELOC = 5
_DIR_DEBUG = 6

DEFAULT_MANIFEST = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"/>
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"/>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"/>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"/>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
</assembly>
"""


class RsrcError(ValueError):
    """The executable layout or an input file is not supported."""


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


# --------------------------------------------------------------------- icon
def icon_resources(ico_path: Path, group_id: int = 1) -> Dict[Tuple[int, int], bytes]:
    """Return ``{(type, id): data}`` for the RT_ICON images and the RT_GROUP_ICON of an .ico file."""
    data = Path(ico_path).read_bytes()
    if len(data) < 6:
        raise RsrcError(f"{ico_path}: not an .ico file")
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1 or count == 0 or len(data) < 6 + 16 * count:
        raise RsrcError(f"{ico_path}: not an .ico file (convert PNG or other formats to .ico first)")
    out: Dict[Tuple[int, int], bytes] = {}
    group = bytearray(struct.pack("<HHH", 0, 1, count))
    for i in range(count):
        w, h, colors, res, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        if offset + size > len(data):
            raise RsrcError(f"{ico_path}: image {i} is truncated")
        out[(RT_ICON, i + 1)] = data[offset : offset + size]
        group += struct.pack("<BBBBHHIH", w, h, colors, res, planes, bits, size, i + 1)
    out[(RT_GROUP_ICON, group_id)] = bytes(group)
    return out


# ------------------------------------------------------------------ version
def _vs_node(key: str, value: bytes = b"", *, text: bool = False, children: Sequence[bytes] = ()) -> bytes:
    """Serialize one VS_VERSIONINFO-style node (``wLength, wValueLength, wType, szKey, Value, Children``)."""
    head_key = key.encode("utf-16-le") + b"\0\0"
    body = bytearray(struct.pack("<HHH", 0, 0, 0)) + head_key
    body += b"\0" * (_align(len(body), 4) - len(body))
    if value:
        body += value
        body += b"\0" * (_align(len(body), 4) - len(body))
    for child in children:
        body += b"\0" * (_align(len(body), 4) - len(body))
        body += child
    value_len = len(value) // 2 if text else len(value)
    struct.pack_into("<HHH", body, 0, len(body), value_len, 1 if text else 0)
    return bytes(body)


def _vs_string(key: str, text: str) -> bytes:
    return _vs_node(key, text.encode("utf-16-le") + b"\0\0", text=True)


def version_resource(
    file_version: Tuple[int, int, int, int],
    strings: Iterable[Tuple[str, str]],
    *,
    product_version: Optional[Tuple[int, int, int, int]] = None,
    file_type: int = 1,
) -> bytes:
    """Build a VS_VERSIONINFO block (US English, Unicode code page)."""
    fv = file_version
    pv = product_version or file_version
    fixed = struct.pack(
        "<13I",
        0xFEEF04BD,
        0x00010000,
        (fv[0] << 16) | fv[1],
        (fv[2] << 16) | fv[3],
        (pv[0] << 16) | pv[1],
        (pv[2] << 16) | pv[3],
        0x3F,
        0,
        0x40004,  # VOS_NT_WINDOWS32
        file_type,  # VFT_APP
        0,
        0,
        0,
    )
    table = _vs_node("040904B0", text=True, children=[_vs_string(k, v) for k, v in strings])
    string_info = _vs_node("StringFileInfo", text=True, children=[table])
    var = _vs_node("Translation", struct.pack("<HH", 0x0409, 1200))
    var_info = _vs_node("VarFileInfo", text=True, children=[var])
    return _vs_node("VS_VERSION_INFO", fixed, children=[string_info, var_info])


# ---------------------------------------------------------- resource tree
def build_resource_section(resources: Dict[Tuple[int, int], bytes], rva: int) -> bytes:
    """Serialize ``{(type_id, name_id): data}`` into raw ``.rsrc`` bytes placed at ``rva``.

    All names are integer IDs with a single neutral language entry.
    """
    tree: Dict[int, Dict[int, bytes]] = {}
    for (typ, name), blob in resources.items():
        tree.setdefault(int(typ), {})[int(name)] = blob

    def dir_size(n: int) -> int:
        return 16 + 8 * n

    # Pass 1: offsets of every directory, then data entries, then blobs.
    types = sorted(tree)
    offset = dir_size(len(types))
    type_dir_off: Dict[int, int] = {}
    for t in types:
        type_dir_off[t] = offset
        offset += dir_size(len(tree[t]))
    name_dir_off: Dict[Tuple[int, int], int] = {}
    for t in types:
        for n in sorted(tree[t]):
            name_dir_off[(t, n)] = offset
            offset += dir_size(1)
    entry_off: Dict[Tuple[int, int], int] = {}
    for t in types:
        for n in sorted(tree[t]):
            entry_off[(t, n)] = offset
            offset += 16
    blob_off: Dict[Tuple[int, int], int] = {}
    for t in types:
        for n in sorted(tree[t]):
            offset = _align(offset, 8)
            blob_off[(t, n)] = offset
            offset += len(tree[t][n])
    out = bytearray(_align(offset, 8))

    def put_dir(at: int, entries: List[Tuple[int, int]]) -> None:
        struct.pack_into("<IIHHHH", out, at, 0, 0, 4, 0, 0, len(entries))
        for i, (ident, target) in enumerate(entries):
            struct.pack_into("<II", out, at + 16 + 8 * i, ident, target)

    put_dir(0, [(t, 0x80000000 | type_dir_off[t]) for t in types])
    for t in types:
        put_dir(type_dir_off[t], [(n, 0x80000000 | name_dir_off[(t, n)]) for n in sorted(tree[t])])
        for n in sorted(tree[t]):
            put_dir(name_dir_off[(t, n)], [(LANG_NEUTRAL, entry_off[(t, n)])])
            blob = tree[t][n]
            struct.pack_into("<IIII", out, entry_off[(t, n)], rva + blob_off[(t, n)], len(blob), 0, 0)
            out[blob_off[(t, n)] : blob_off[(t, n)] + len(blob)] = blob
    return bytes(out)


# ------------------------------------------------------------- PE editing
class _Section:
    __slots__ = ("name", "vsize", "va", "raw_size", "raw_ptr", "rest", "chars", "data")

    def __init__(self, raw: bytes, data: bytes):
        (self.name, self.vsize, self.va, self.raw_size, self.raw_ptr) = struct.unpack_from("<8sIIII", raw, 0)
        self.rest = raw[24:36]  # relocations/line numbers (unused in images)
        self.chars = struct.unpack_from("<I", raw, 36)[0]
        self.data = data

    def header(self) -> bytes:
        return struct.pack("<8sIIII", self.name, self.vsize, self.va, self.raw_size, self.raw_ptr) + self.rest + struct.pack(
            "<I", self.chars
        )


def add_resources(image: bytes, resources: Dict[Tuple[int, int], bytes], *, timestamp: Optional[int] = None) -> bytes:
    """Return ``image`` (a PE32+ executable without overlay) with a new ``.rsrc`` section."""
    if image[:2] != b"MZ":
        raise RsrcError("not a PE file (missing MZ header)")
    pe_off = struct.unpack_from("<I", image, 0x3C)[0]
    if image[pe_off : pe_off + 4] != b"PE\0\0":
        raise RsrcError("not a PE file (missing PE signature)")
    coff = pe_off + 4
    nsec = struct.unpack_from("<H", image, coff + 2)[0]
    opt_size = struct.unpack_from("<H", image, coff + 16)[0]
    opt = coff + 20
    if struct.unpack_from("<H", image, opt)[0] != 0x20B:
        raise RsrcError("only 64-bit (PE32+) executables are supported")
    sect_align, file_align = struct.unpack_from("<II", image, opt + 32)
    size_of_headers = struct.unpack_from("<I", image, opt + 60)[0]
    n_dirs = struct.unpack_from("<I", image, opt + 108)[0]
    dirs_off = opt + 112

    def get_dir(i: int) -> Tuple[int, int]:
        return struct.unpack_from("<II", image, dirs_off + 8 * i) if i < n_dirs else (0, 0)

    if get_dir(_DIR_RESOURCE)[0]:
        raise RsrcError("executable already has resources; expected a stock PyInstaller bootloader")
    if get_dir(_DIR_SECURITY)[0]:
        raise RsrcError("executable is signed; resources cannot be added")

    sec_table = opt + opt_size
    sections: List[_Section] = []
    for i in range(nsec):
        raw = image[sec_table + 40 * i : sec_table + 40 * (i + 1)]
        s = _Section(raw, b"")
        s.data = image[s.raw_ptr : s.raw_ptr + s.raw_size]
        sections.append(s)
    end_of_sections = max((s.raw_ptr + s.raw_size for s in sections), default=size_of_headers)
    if len(image) > end_of_sections:
        raise RsrcError("executable has appended data; add resources before appending the archive")
    if sec_table + 40 * (nsec + 1) > size_of_headers:
        raise RsrcError("no room for another section header")

    reloc: Optional[_Section] = None
    if sections and sections[-1].name.rstrip(b"\0") == b".reloc" and get_dir(_DIR_BASERELOC)[0] == sections[-1].va:
        reloc = sections.pop()

    last = sections[-1]
    rsrc_va = _align(last.va + max(last.vsize, last.raw_size), sect_align)
    rsrc_raw_ptr = _align(last.raw_ptr + last.raw_size, file_align)
    rsrc_data = build_resource_section(resources, rsrc_va)
    rsrc = _Section(b"\0" * 40, b"")
    rsrc.name = b".rsrc\0\0\0"
    rsrc.vsize = len(rsrc_data)
    rsrc.va = rsrc_va
    rsrc.raw_size = _align(len(rsrc_data), file_align)
    rsrc.raw_ptr = rsrc_raw_ptr
    rsrc.chars = _SCN_RSRC
    rsrc.data = rsrc_data + b"\0" * (rsrc.raw_size - len(rsrc_data))
    sections.append(rsrc)
    if reloc is not None:
        reloc.va = _align(rsrc.va + rsrc.vsize, sect_align)
        reloc.raw_ptr = rsrc.raw_ptr + rsrc.raw_size
        sections.append(reloc)

    out = bytearray(image[:size_of_headers])
    for s in sections:
        if s.raw_ptr > len(out):
            out += b"\0" * (s.raw_ptr - len(out))
        out[s.raw_ptr : s.raw_ptr + len(s.data)] = s.data
    for i, s in enumerate(sections):
        out[sec_table + 40 * i : sec_table + 40 * (i + 1)] = s.header()
    struct.pack_into("<H", out, coff + 2, len(sections))
    struct.pack_into("<II", out, dirs_off + 8 * _DIR_RESOURCE, rsrc.va, len(rsrc_data))
    if reloc is not None:
        size = get_dir(_DIR_BASERELOC)[1]
        struct.pack_into("<II", out, dirs_off + 8 * _DIR_BASERELOC, reloc.va, size)
    final = sections[-1]
    struct.pack_into("<I", out, opt + 56, _align(final.va + max(final.vsize, final.raw_size), sect_align))  # SizeOfImage
    init_data = sum(s.raw_size for s in sections if s.chars & _SCN_INIT_DATA)
    struct.pack_into("<I", out, opt + 8, init_data)  # SizeOfInitializedData
    if timestamp is not None:
        _set_timestamp(out, coff, sections, get_dir(_DIR_DEBUG), int(timestamp))
    return bytes(out)


def _set_timestamp(out: bytearray, coff: int, sections: List[_Section], debug_dir: Tuple[int, int], ts: int) -> None:
    struct.pack_into("<I", out, coff + 4, ts & 0xFFFFFFFF)
    rva, size = debug_dir
    if not rva:
        return
    for s in sections:
        if s.va <= rva < s.va + max(s.vsize, s.raw_size):
            base = s.raw_ptr + (rva - s.va)
            for i in range(size // 28):
                at = base + 28 * i + 4
                if struct.unpack_from("<I", out, at)[0]:
                    struct.pack_into("<I", out, at, ts & 0xFFFFFFFF)
            return


def build_timestamp() -> int:
    """Current time, or ``SOURCE_DATE_EPOCH`` for reproducible builds (as PyInstaller does)."""
    return int(os.environ.get("SOURCE_DATE_EPOCH", time.time()))


# --------------------------------------------------------------- checksum
def pe_checksum(path: Path) -> int:
    """Compute the PE optional-header checksum (same algorithm as ``MapFileAndCheckSum``)."""
    buf = bytearray(Path(path).read_bytes())
    length = len(buf)
    pe_off = struct.unpack_from("<I", buf, 0x3C)[0]
    cks_off = pe_off + 4 + 20 + 64
    if length % 2:
        buf += b"\0"
    buf[cks_off : cks_off + 4] = b"\0\0\0\0"
    total = 0
    mv = memoryview(buf).cast("H")
    chunk = 1 << 16
    for i in range(0, len(mv), chunk):
        total += sum(mv[i : i + chunk])
        total = (total & 0xFFFFFFFF) + (total >> 32)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (total + length) & 0xFFFFFFFF


def update_checksum(path: Path) -> int:
    path = Path(path)
    value = pe_checksum(path)
    with open(path, "r+b") as fh:
        head = fh.read(0x40)
        pe_off = struct.unpack_from("<I", head, 0x3C)[0]
        fh.seek(pe_off + 4 + 20 + 64)
        fh.write(struct.pack("<I", value))
    return value
