"""Build tiny synthetic PE files for tests (no real executable code)."""

from __future__ import annotations

import struct
from typing import Iterable, Optional, Sequence

from pyi_doctor.pe import PYINSTALLER_COOKIE

FILE_ALIGN = 0x200
SECT_ALIGN = 0x1000


def build_pe(
    *,
    sections: Sequence[str] = (".text", ".rdata"),
    resource_types: Iterable[int] = (),
    pe32_plus: bool = True,
    machine: int = 0x8664,
    overlay: bytes = b"",
    cookie: bool = False,
    signature: Optional[bytes] = None,
) -> bytes:
    res_types = list(resource_types)
    names = list(sections) + ([".rsrc"] if res_types else [])
    n = len(names)
    opt_size = 240 if pe32_plus else 224
    headers = bytearray(0x400)
    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, 0x40)
    headers[0x40:0x44] = b"PE\0\0"
    coff = 0x44
    struct.pack_into("<HHIIIHH", headers, coff, machine, n, 0, 0, 0, opt_size, 0x22)
    opt = coff + 20
    struct.pack_into("<H", headers, opt, 0x20B if pe32_plus else 0x10B)
    count_off = 108 if pe32_plus else 92
    struct.pack_into("<I", headers, opt + count_off, 16)
    dirs = opt + count_off + 4

    body = bytearray()
    sec_table = opt + opt_size
    res_rva = 0
    for i, name in enumerate(names):
        va = SECT_ALIGN * (i + 1)
        raw_ptr = 0x400 + i * FILE_ALIGN
        raw = bytearray(FILE_ALIGN)
        if name == ".rsrc":
            res_rva = va
            struct.pack_into("<IIHHHH", raw, 0, 0, 0, 4, 0, 0, len(res_types))
            for j, t in enumerate(res_types):
                struct.pack_into("<II", raw, 16 + j * 8, t, 0x80000000 | 0x100)
        body += raw
        entry = sec_table + i * 40
        headers[entry : entry + 8] = name.encode("latin-1")[:8].ljust(8, b"\0")
        struct.pack_into("<IIII", headers, entry + 8, FILE_ALIGN, va, FILE_ALIGN, raw_ptr)
    if res_rva:
        struct.pack_into("<II", headers, dirs + 2 * 8, res_rva, 16 + 8 * len(res_types))

    data = bytes(headers) + bytes(body) + overlay
    if cookie:
        data += b"\0" * 32 + PYINSTALLER_COOKIE + b"\0" * 80
    if signature is not None:
        sig_off = len(data)
        data += signature
        hdr = bytearray(data[:0x400])
        struct.pack_into("<II", hdr, dirs + 4 * 8, sig_off, len(signature))
        data = bytes(hdr) + data[0x400:]
    return data
