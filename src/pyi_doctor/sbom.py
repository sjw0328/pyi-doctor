"""CycloneDX 1.5 JSON software bill of materials for the bundled packages."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import Project
from .licenses import PackageLicense

_SPDX_EXPR = re.compile(r"^[A-Za-z0-9.+\-() ]+$")


def purl(name: str, version: str) -> str:
    norm = re.sub(r"[-_.]+", "-", name).lower()
    return f"pkg:pypi/{norm}@{version}"


def _license_entry(lic: str) -> List[dict]:
    if not lic or lic == "UNKNOWN":
        return []
    if _looks_spdx(lic):
        return [{"license": {"id": lic}}]
    if (" OR " in lic or " AND " in lic or " WITH " in lic) and _SPDX_EXPR.match(lic):
        return [{"expression": lic}]
    return [{"license": {"name": lic}}]


_COMMON_SPDX = {
    "MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "MPL-2.0", "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0-only", "LGPL-3.0-or-later", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
    "PSF-2.0", "Python-2.0", "Unlicense", "0BSD", "Zlib", "CC0-1.0", "EPL-2.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "BSL-1.0", "HPND", "MIT-0",
}


def _looks_spdx(lic: str) -> bool:
    return lic in _COMMON_SPDX


def build(project: Project, packages: List[PackageLicense], *, serial: Optional[str] = None, timestamp: Optional[str] = None) -> dict:
    ts = timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    components = []
    for p in packages:
        comp = {
            "type": "library",
            "bom-ref": purl(p.name, p.version),
            "name": p.name,
            "version": p.version,
            "purl": purl(p.name, p.version),
        }
        lic = _license_entry(p.license)
        if lic:
            comp["licenses"] = lic
        if p.homepage:
            comp["externalReferences"] = [{"type": "website", "url": p.homepage}]
        components.append(comp)
    app = {"type": "application", "bom-ref": f"app:{project.name}", "name": project.name, "version": project.version}
    if project.publisher:
        app["publisher"] = project.publisher
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial or f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": ts,
            "tools": {"components": [{"type": "application", "name": "pyi-doctor", "version": __version__}]},
            "component": app,
        },
        "components": components,
        "dependencies": [{"ref": app["bom-ref"], "dependsOn": [c["bom-ref"] for c in components]}],
    }


def write(path: Path, project: Project, packages: List[PackageLicense]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(project, packages), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
