import json

import pytest

from pyi_doctor import licenses, sbom
from pyi_doctor.licenses import PackageLicense

TOC_SAMPLE = """(['/src/main.py'],
 ['/src'],
 [],
 [('pytest', '/site/pytest/__init__.py', 'PYMODULE'),
  ('_pytest.config', '/site/_pytest/config/__init__.py', 'PYMODULE'),
  ('json', '/usr/lib/json/__init__.py', 'PYMODULE'),
  ('certifi/cacert.pem', '/site/certifi/cacert.pem', 'DATA'),
  ('PIL\\\\_imaging.pyd', 'C:/site/PIL/_imaging.pyd', 'EXTENSION'),
  ('six.py', '/site/six.py', 'DATA'),
  ('libz.so.1', '/lib/libz.so.1', 'BINARY')],
 [('main', '/src/main.py', 'PYSOURCE')])
"""


def test_modules_from_toc_text():
    mods = licenses.modules_from_toc_text(TOC_SAMPLE)
    assert {"pytest", "_pytest", "json", "certifi", "PIL", "six", "main", "libz.so.1"} <= mods


def test_modules_from_toc_regex_fallback():
    text = "garbage(\n[('requests.api', '/site/requests/api.py', 'PYMODULE'), ('x', None, 'BINARY')] not python {"
    mods = licenses.modules_from_toc_text(text)
    assert "requests" in mods and "x" in mods


def test_bundled_modules_from_folder(tmp_path):
    (tmp_path / "onedir").mkdir()
    (tmp_path / "onedir" / "Analysis-00.toc").write_text(TOC_SAMPLE, encoding="utf-8")
    assert "pytest" in licenses.bundled_modules(tmp_path)


def test_collect_toc_mode_maps_to_distribution(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "PYZ-00.toc").write_text(TOC_SAMPLE, encoding="utf-8")
    pkgs, mode = licenses.collect(work, exclude=[])
    assert mode == "toc"
    # pytest itself is a build/test tool and excluded by default
    assert all(p.name.lower() != "pytest" for p in pkgs)
    # with the default exclusion list removed it must be found via its top-level modules
    from importlib.metadata import distribution

    pkg = licenses.package_license(distribution("pytest"))
    assert pkg.name.lower() == "pytest" and pkg.license not in ("", "UNKNOWN")
    assert pkg.license_files, "pytest ships a LICENSE file in its dist-info"


def test_collect_environment_mode(tmp_path):
    pkgs, mode = licenses.collect(tmp_path / "does-not-exist")
    assert mode == "environment"
    names = {p.name.lower() for p in pkgs}
    assert "pyinstaller" not in names and "pip" not in names


def test_render_and_write(tmp_path):
    pkgs = [
        PackageLicense("alpha", "1.0", "MIT", [("LICENSE", "MIT License text")], "https://a.example"),
        PackageLicense("beta", "2.0", "UNKNOWN"),
    ]
    out = licenses.write(tmp_path / "N.txt", pkgs, app_name="App", mode="toc")
    text = out.read_text(encoding="utf-8")
    assert "Third-party software notices for App" in text
    assert "PyInstaller bootloader" in text and "Python " in text
    assert "alpha 1.0" in text and "MIT License text" in text and "Homepage: https://a.example" in text
    assert "no license file found" in text
    assert licenses.summary(pkgs) == {"MIT": 1, "UNKNOWN": 1}


class _Meta(dict):
    def get_all(self, key):
        v = self.get(key)
        return v if isinstance(v, list) else None


@pytest.mark.parametrize("meta,expected", [
    ({"License-Expression": "Apache-2.0 OR MIT"}, "Apache-2.0 OR MIT"),
    ({"License": "BSD"}, "BSD"),
    ({"License": "UNKNOWN", "Classifier": ["License :: OSI Approved :: MIT License"]}, "MIT License"),
    ({"License": "A" * 500}, "see license text"),
    ({}, "UNKNOWN"),
])
def test_license_string(meta, expected):
    assert licenses._license_string(_Meta(meta)) == expected


def test_sbom_structure(make_project):
    p = make_project(name="App", version="1.0.0", publisher="Pub")
    pkgs = [
        PackageLicense("Some_Pkg", "1.2", "MIT", homepage="https://x"),
        PackageLicense("dual", "3", "Apache-2.0 OR MIT"),
        PackageLicense("odd", "1", "Custom License v2"),
        PackageLicense("none", "0.1", "UNKNOWN"),
    ]
    bom = sbom.build(p, pkgs, serial="urn:uuid:00000000-0000-0000-0000-000000000000", timestamp="2026-01-01T00:00:00Z")
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.5"
    assert bom["metadata"]["component"]["name"] == "App"
    comps = {c["name"]: c for c in bom["components"]}
    assert comps["Some_Pkg"]["purl"] == "pkg:pypi/some-pkg@1.2"
    assert comps["Some_Pkg"]["licenses"] == [{"license": {"id": "MIT"}}]
    assert comps["dual"]["licenses"] == [{"expression": "Apache-2.0 OR MIT"}]
    assert comps["odd"]["licenses"] == [{"license": {"name": "Custom License v2"}}]
    assert "licenses" not in comps["none"]
    assert bom["dependencies"][0]["dependsOn"] == [c["bom-ref"] for c in bom["components"]]
    json.dumps(bom)


def test_sbom_write(make_project, tmp_path):
    out = sbom.write(tmp_path / "x.cdx.json", make_project(), [])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["components"] == [] and data["serialNumber"].startswith("urn:uuid:")
