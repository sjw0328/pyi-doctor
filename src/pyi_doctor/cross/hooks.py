"""Run PyInstaller hooks against a folder instead of an installed environment.

PyInstaller hooks normally import the package (in a subprocess) to find
submodules, data files and metadata. Windows wheels cannot be imported on
Linux/macOS, so the hook file is executed with replacements that answer the
same questions by reading the unpacked wheel folder. ``sys.platform``,
``os.name`` and ``PyInstaller.compat.is_win`` report Windows.

Hooks that use anything else (importing the package, ``hook(hook_api)``
functions, other helper functions) raise :class:`UnsupportedHook`; the caller
stops the build and reports them.
"""

from __future__ import annotations

import builtins
import fnmatch
import glob as _glob
import logging
import os
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

DataList = List[Tuple[str, str]]

EXT_SUFFIXES = [f".cp{sys.version_info.major}{sys.version_info.minor}-win_amd64.pyd", ".pyd"]
SOURCE_SUFFIXES = [".py"]
BYTECODE_SUFFIXES = [".pyc"]
ALL_SUFFIXES = EXT_SUFFIXES + SOURCE_SUFFIXES + BYTECODE_SUFFIXES

_SAFE_MODULES = {"re", "fnmatch", "glob", "pathlib", "warnings", "logging", "os.path", "collections", "itertools",
                 "functools", "textwrap", "string"}


class UnsupportedHook(Exception):
    """The hook needs something only an importable (Windows) environment can provide."""


@dataclass
class HookResult:
    hiddenimports: List[str] = field(default_factory=list)
    excludedimports: List[str] = field(default_factory=list)
    datas: DataList = field(default_factory=list)
    binaries: DataList = field(default_factory=list)


def _norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class Site:
    """Answers module/package/distribution questions from folders on disk."""

    def __init__(self, search_dirs: Sequence[Path], site_dir: Path, builtins_: Iterable[str] = ()):
        self.search_dirs = [Path(p) for p in search_dirs]
        self.site_dir = Path(site_dir)
        self.builtins = set(builtins_)
        self._dists: Optional[Dict[str, Path]] = None

    # ---------------------------------------------------------------- modules
    def _module_in(self, base: Path, part: str) -> Optional[Tuple[Path, bool]]:
        pkg = base / part
        for init in ("__init__.py", "__init__.pyc"):
            if (pkg / init).is_file():
                return pkg, True
        for suf in ALL_SUFFIXES:
            f = base / (part + suf)
            if f.is_file():
                return f, False
        if pkg.is_dir():
            return pkg, True  # namespace package
        return None

    def locate(self, name: str) -> Optional[Tuple[Path, bool]]:
        """Return (path, is_package) for a dotted module name, or None."""
        parts = name.split(".")
        for base in self.search_dirs:
            found = self._module_in(base, parts[0])
            if not found:
                continue
            path, is_pkg = found
            ok = True
            for part in parts[1:]:
                if not is_pkg:
                    ok = False
                    break
                sub = self._module_in(path, part)
                if not sub:
                    ok = False
                    break
                path, is_pkg = sub
            if ok:
                return path, is_pkg
        return None

    def package_dir(self, name: str) -> Path:
        found = self.locate(name)
        if not found or not found[1]:
            raise UnsupportedHook(f"package {name!r} not found in the Windows wheels")
        return found[0]

    def can_import(self, name: str) -> bool:
        return name in self.builtins or self.locate(name) is not None

    def submodules(self, package: str, flt: Optional[Callable[[str], bool]] = None) -> List[str]:
        found = self.locate(package)
        if not found:
            return []
        path, is_pkg = found
        if not is_pkg:
            return [package] if (flt is None or flt(package)) else []
        out = [package] if (flt is None or flt(package)) else []

        def walk(d: Path, prefix: str) -> None:
            try:
                entries = sorted(d.iterdir())
            except OSError:
                return
            for e in entries:
                if e.name == "__pycache__":
                    continue
                if e.is_dir():
                    if (e / "__init__.py").is_file() or (e / "__init__.pyc").is_file():
                        full = prefix + e.name
                        if flt is not None and not flt(full):
                            continue
                        out.append(full)
                        walk(e, full + ".")
                    continue
                for suf in ALL_SUFFIXES:
                    if e.name.endswith(suf):
                        stem = e.name[: -len(suf)]
                        if stem == "__init__" or "." in stem:
                            break
                        full = prefix + stem
                        if flt is None or flt(full):
                            out.append(full)
                        break

        walk(path, package + ".")
        return list(dict.fromkeys(out))

    # ------------------------------------------------------------ dist-info
    def dists(self) -> Dict[str, Path]:
        if self._dists is None:
            self._dists = {}
            for d in sorted(self.site_dir.glob("*.dist-info")):
                meta = d / "METADATA"
                name = None
                if meta.is_file():
                    for line in meta.read_text("utf-8", "replace").splitlines():
                        if line.lower().startswith("name:"):
                            name = line.split(":", 1)[1].strip()
                            break
                if not name:
                    name = d.name[: -len(".dist-info")].rsplit("-", 1)[0]
                self._dists[_norm_dist(name)] = d
        return self._dists

    def dist_info(self, name: str) -> Optional[Path]:
        return self.dists().get(_norm_dist(name))

    def dist_version(self, name: str) -> Optional[str]:
        d = self.dist_info(name)
        if not d:
            return None
        for line in (d / "METADATA").read_text("utf-8", "replace").splitlines():
            if line.lower().startswith("version:"):
                return line.split(":", 1)[1].strip()
        return None

    def dist_requires(self, name: str) -> List[str]:
        d = self.dist_info(name)
        if not d:
            return []
        out = []
        for line in (d / "METADATA").read_text("utf-8", "replace").splitlines():
            if line.lower().startswith("requires-dist:"):
                spec = line.split(":", 1)[1].strip()
                if "extra" in spec.split(";", 1)[-1] and ";" in spec:
                    continue
                m = re.match(r"[A-Za-z0-9._-]+", spec)
                if m:
                    out.append(m.group(0))
        return out

    def dists_for_package(self, top: str) -> List[str]:
        names = []
        for norm, d in self.dists().items():
            tl = d / "top_level.txt"
            tops = set(tl.read_text("utf-8", "replace").split()) if tl.is_file() else set()
            if not tops:
                rec = d / "RECORD"
                if rec.is_file():
                    for line in rec.read_text("utf-8", "replace").splitlines():
                        first = line.split(",", 1)[0].split("/", 1)[0]
                        if first and not first.endswith((".dist-info", ".data")):
                            tops.add(first.split(".", 1)[0])
            if top in tops:
                names.append(norm)
        return names


# -------------------------------------------------------------- fake helpers
def _make_hooks_module(site: Site) -> types.ModuleType:
    m = types.ModuleType("PyInstaller.utils.hooks")
    m.logger = logging.getLogger("pyi_doctor.cross.hooks")

    def _rel_dest(path: Path, root: Path) -> str:
        return path.parent.relative_to(root).as_posix() or "."

    def _root_of(pkg_dir: Path) -> Path:
        # search folders can be nested (the app folder may contain the wheel folder): take the deepest
        parents = [d for d in site.search_dirs if d in pkg_dir.parents]
        return max(parents, key=lambda d: len(d.parts)) if parents else pkg_dir.parent

    def collect_submodules(package, filter=None, on_error="warn once"):  # noqa: A002
        if not isinstance(package, str):
            raise UnsupportedHook("collect_submodules() with a module object")
        return site.submodules(package, filter)

    def _match_any(rel: str, patterns: Sequence[str]) -> bool:
        for p in patterns:
            p = p.replace("\\", "/")
            if fnmatch.fnmatch(rel, p) or (p.startswith("**/") and fnmatch.fnmatch(rel, p[3:])):
                return True
        return False

    def collect_data_files(package, include_py_files=False, subdir=None, excludes=None, includes=None):
        if not isinstance(package, str):
            raise UnsupportedHook("collect_data_files() with a module object")
        found = site.locate(package)
        if not found or not found[1]:
            return []
        pkg_dir = found[0]
        root = _root_of(pkg_dir)
        base = pkg_dir / subdir if subdir else pkg_dir
        inc = list(includes or ["**/*"])
        exc = list(excludes or [])
        if not include_py_files:
            exc += ["**/*" + s for s in ALL_SUFFIXES]
        exc += ["**/__pycache__/**", "**/__pycache__"]
        out = []
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(pkg_dir).as_posix()
            if not _match_any(rel, inc) or _match_any(rel, exc):
                continue
            out.append((str(f), _rel_dest(f, root)))
        return out

    def collect_dynamic_libs(package, destdir=None, search_patterns=("*.dll",)):
        found = site.locate(package)
        if not found or not found[1]:
            return []
        pkg_dir = found[0]
        root = _root_of(pkg_dir)
        out = []
        for f in sorted(pkg_dir.rglob("*")):
            if f.is_file() and any(fnmatch.fnmatch(f.name.lower(), p.lower()) for p in search_patterns if p.endswith(".dll")):
                out.append((str(f), destdir or _rel_dest(f, root)))
        return out

    def copy_metadata(package_name, recursive=False):
        seen, out, todo = set(), [], [package_name]
        while todo:
            name = todo.pop()
            if _norm_dist(name) in seen:
                continue
            seen.add(_norm_dist(name))
            d = site.dist_info(name)
            if d is None:
                if name == package_name:
                    raise UnsupportedHook(f"copy_metadata({package_name!r}): distribution not found in the Windows wheels")
                continue
            out.append((str(d), d.name))
            if recursive:
                todo.extend(site.dist_requires(name))
        return out

    def collect_all(package_name, include_py_files=True, filter_submodules=None, exclude_datas=None,
                    include_datas=None, on_error="warn once"):
        datas = collect_data_files(package_name, include_py_files, excludes=exclude_datas, includes=include_datas)
        for dist in site.dists_for_package(package_name.split(".")[0]):
            datas += copy_metadata(dist)
        binaries = collect_dynamic_libs(package_name)
        hidden = collect_submodules(package_name, filter_submodules)
        return datas, binaries, hidden

    def collect_delvewheel_libs_directory(package_name, libdir_name=None, datas=None, binaries=None):
        datas = list(datas or [])
        binaries = list(binaries or [])
        dist_names = site.dists_for_package(package_name.split(".")[0]) or [package_name]
        cands = [libdir_name] if libdir_name else []
        cands += [f"{package_name}.libs"] + [f"{n}.libs" for n in dist_names] + [f"{n.replace('-', '_')}.libs" for n in dist_names]
        for c in cands:
            d = site.site_dir / c
            if d.is_dir():
                for f in sorted(d.iterdir()):
                    if f.is_file():
                        binaries.append((str(f), c))
                break
        return datas, binaries

    def is_module_satisfies(requirements, version=None, version_attr=None):
        if version is not None or version_attr is not None:
            raise UnsupportedHook("is_module_satisfies() with version arguments")
        try:
            from packaging.requirements import Requirement
        except ImportError:  # pragma: no cover
            from pip._vendor.packaging.requirements import Requirement  # type: ignore
        req = Requirement(requirements)
        ver = site.dist_version(req.name)
        if ver is None:
            return False
        return req.specifier.contains(ver, prereleases=True)

    def get_module_file_attribute(package):
        found = site.locate(package)
        if not found:
            raise ImportError(package)
        path, is_pkg = found
        if is_pkg:
            for init in ("__init__.py", "__init__.pyc"):
                if (path / init).is_file():
                    return str(path / init)
            raise ImportError(package)
        return str(path)

    def get_package_paths(package):
        pkg_dir = site.package_dir(package)
        return str(_root_of(pkg_dir)), str(pkg_dir)

    def is_package(name):
        found = site.locate(name)
        return bool(found and found[1])

    def remove_prefix(s, prefix):
        return s[len(prefix):] if s.startswith(prefix) else s

    def remove_suffix(s, suffix):
        return s[: -len(suffix)] if suffix and s.endswith(suffix) else s

    def remove_file_extension(filename):
        for suf in ALL_SUFFIXES:
            if filename.endswith(suf):
                return filename[: -len(suf)]
        return os.path.splitext(filename)[0]

    for fn in (collect_submodules, collect_data_files, collect_dynamic_libs, copy_metadata, collect_all,
               collect_delvewheel_libs_directory, is_module_satisfies, get_module_file_attribute, get_package_paths,
               is_package, remove_prefix, remove_suffix, remove_file_extension):
        setattr(m, fn.__name__, fn)
    m.check_requirement = is_module_satisfies
    m.can_import_module = site.can_import

    def get_installer(module):
        # Wheels unpacked by pyi-doctor were downloaded with pip.
        return "pip"

    m.get_installer = get_installer

    def _missing(name):
        if name.startswith("__"):
            raise AttributeError(name)

        # Unknown helpers fail only when called, so hooks that import them for another
        # package version (and never call them) still work.
        def _stub(*args, **kwargs):
            raise UnsupportedHook(f"calls PyInstaller.utils.hooks.{name}()")

        return _stub

    m.__getattr__ = _missing  # type: ignore[attr-defined]
    return m


def _make_importlib_metadata(site: Site) -> types.ModuleType:
    m = types.ModuleType("importlib_metadata")

    class PackageNotFoundError(ModuleNotFoundError):
        pass

    def version(name):
        v = site.dist_version(name)
        if v is None:
            raise PackageNotFoundError(name)
        return v

    m.PackageNotFoundError = PackageNotFoundError
    m.version = version
    return m


class _Strict(types.ModuleType):
    """Module proxy: listed attributes, then a fallback object, else UnsupportedHook."""

    def __init__(self, name: str, values: Dict[str, object], fallback: object = None):
        super().__init__(name)
        self.__dict__.update(values)
        object.__setattr__(self, "_fallback", fallback)

    def __getattr__(self, item):
        fb = object.__getattribute__(self, "_fallback")
        if fb is not None and hasattr(fb, item):
            return getattr(fb, item)
        raise UnsupportedHook(f"uses {self.__name__}.{item}")


def _make_compat(site: Site) -> types.ModuleType:
    from PyInstaller import compat as real

    values = {k: getattr(real, k) for k in dir(real) if re.fullmatch(r"is_py\d+", k)}
    values.update(
        is_win=True, is_win_10=True, is_win_11=False, is_cygwin=False, is_darwin=False, is_linux=False,
        is_freebsd=False, is_openbsd=False, is_solar=False, is_aix=False, is_hpux=False, is_unix=False,
        is_musl=False, is_termux=False, is_android=False, is_ios=False, is_64bits=True, is_conda=False,
        is_venv=False, is_virtualenv=False, is_pure_conda=False, is_nogil=False, is_win_wine=False,
        is_macos_11=False, is_macos_11_compat=False, is_macos_11_native=False,
        EXTENSION_SUFFIXES=EXT_SUFFIXES, ALL_SUFFIXES=ALL_SUFFIXES, PYDYLIB_NAMES={f"python{sys.version_info.major}{sys.version_info.minor}.dll"},
        architecture="64bit", machine="intel", system="Windows",
        importlib_metadata=_make_importlib_metadata(site),
    )
    return _Strict("PyInstaller.compat", values)


def _make_sys() -> types.ModuleType:
    values = {k: getattr(sys, k) for k in ("version_info", "version", "hexversion", "maxsize", "byteorder",
                                           "implementation", "flags", "float_info", "int_info", "maxunicode")}
    values.update(platform="win32", builtin_module_names=(), modules={}, path=[], executable="python.exe")
    return _Strict("sys", values)


def _make_os() -> types.ModuleType:
    return _Strict("os", {"name": "nt", "path": os.path, "sep": "\\", "pathsep": ";", "linesep": "\r\n",
                          "environ": {}, "getenv": lambda k, d=None: d}, fallback=_OsReadOnly())


class _OsReadOnly:
    """Read-only os functions a hook may reasonably use."""

    listdir = staticmethod(os.listdir)
    walk = staticmethod(os.walk)
    scandir = staticmethod(os.scandir)
    fspath = staticmethod(os.fspath)
    stat = staticmethod(os.stat)


def _make_sysconfig() -> types.ModuleType:
    # On Windows ``sysconfig._get_sysconfigdata_name`` fails; PyInstaller's hook handles AttributeError.
    m = types.ModuleType("sysconfig")

    def _ga(item):
        raise AttributeError(item)

    m.__getattr__ = _ga  # type: ignore[attr-defined]
    return m


def _make_mypy_helper(site: Site) -> types.ModuleType:
    """``_pyinstaller_hooks_contrib.utils.mypy`` answered from the wheel's RECORD."""
    m = types.ModuleType("_pyinstaller_hooks_contrib.utils.mypy")

    def find_mypyc_module_for_dist(dist_name):
        d = site.dist_info(dist_name)
        if d is None or not (d / "RECORD").is_file():
            return []
        out = []
        for line in (d / "RECORD").read_text("utf-8", "replace").splitlines():
            name = line.split(",", 1)[0].rsplit("/", 1)[-1]
            if "__mypyc" in name:
                out.append(name.split(".")[0])
        return out

    m.find_mypyc_module_for_dist = find_mypyc_module_for_dist
    return m


class HookRunner:
    def __init__(self, site: Site, hook_dirs: Sequence[Path], pre_dirs: Sequence[Path]):
        self.site = site
        self.hook_dirs = [Path(d) for d in hook_dirs if Path(d).is_dir()]
        self.pre_dirs = [Path(d) for d in pre_dirs if Path(d).is_dir()]
        self._hooks_mod = _make_hooks_module(site)
        self._compat = _make_compat(site)
        pyi = types.ModuleType("PyInstaller")
        pyi.compat = self._compat
        utils = types.ModuleType("PyInstaller.utils")
        utils.hooks = self._hooks_mod
        pyi.utils = utils
        self._fake = {
            "PyInstaller": pyi,
            "PyInstaller.utils": utils,
            "PyInstaller.utils.hooks": self._hooks_mod,
            "PyInstaller.compat": self._compat,
            "sys": _make_sys(),
            "os": _make_os(),
            "sysconfig": _make_sysconfig(),
            "_pyinstaller_hooks_contrib.utils.mypy": _make_mypy_helper(site),
        }

    def find(self, module: str) -> Optional[Path]:
        for d in self.hook_dirs:
            f = d / f"hook-{module}.py"
            if f.is_file():
                return f
        return None

    def pre_hook_problem(self, module: str) -> Optional[str]:
        """Describe a pre-import hook that cannot be honoured, or ``None``."""
        for d in self.pre_dirs:
            f = d / f"hook-{module}.py"
            if not f.is_file():
                continue
            src = f.read_text("utf-8", "replace")
            # setuptools-vendoring alias hooks are no-ops unless setuptools' vendored copy is used.
            if "PyInstaller.utils.hooks.setuptools import pre_safe_import_module" in src:
                continue
            if module == "_pyi_rth_utils":
                continue  # handled by putting PyInstaller's fake-modules folder on the search path
            return f"{module}: needs PyInstaller pre-import hook {d.name}/{f.name}"
        return None

    def _import(self, name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            raise UnsupportedHook(f"relative import of {name!r}")
        if name in self._fake:
            mod = self._fake[name]
            if fromlist or "." not in name:
                return mod
            return self._fake[name.split(".")[0]]
        if name in _SAFE_MODULES or name.split(".")[0] in {"re", "glob", "pathlib", "warnings", "logging", "fnmatch",
                                                           "packaging"}:
            return builtins.__import__(name, globals, locals, fromlist, level)
        raise UnsupportedHook(f"imports {name!r}")

    def run(self, hook_file: Path) -> HookResult:
        src = hook_file.read_text("utf-8", "replace")
        code = compile(src, str(hook_file), "exec")
        b = dict(vars(builtins))
        b["__import__"] = self._import
        b["open"] = _no_open
        ns: Dict[str, object] = {"__builtins__": b, "__name__": "__pyi_doctor_hook__", "__file__": str(hook_file)}
        try:
            exec(code, ns)
        except UnsupportedHook:
            raise
        except Exception as exc:  # the hook expected a real environment
            raise UnsupportedHook(f"failed: {type(exc).__name__}: {exc}") from exc
        if callable(ns.get("hook")):
            raise UnsupportedHook("defines hook(hook_api)")
        mode = ns.get("module_collection_mode")
        if mode and mode != "pyz" and not (isinstance(mode, dict) and set(mode.values()) <= {"pyz"}):
            raise UnsupportedHook(f"sets module_collection_mode={mode!r}")
        res = HookResult()
        res.hiddenimports = [str(x) for x in (ns.get("hiddenimports") or [])]
        res.excludedimports = [str(x) for x in (ns.get("excludedimports") or [])]
        res.datas = _expand(ns.get("datas") or [], hook_file)
        res.binaries = _expand(ns.get("binaries") or [], hook_file)
        return res


def _no_open(*args, **kwargs):
    raise UnsupportedHook("reads files with open()")


def _expand(items, hook_file: Path) -> DataList:
    """Expand (source-glob, dest) pairs like PyInstaller's ``format_binaries_and_datas``."""
    out: DataList = []
    for item in items:
        try:
            src, dest = item[0], item[1]
        except (TypeError, IndexError):
            raise UnsupportedHook(f"invalid datas/binaries entry {item!r}") from None
        src = os.fspath(src)
        if not os.path.isabs(src):
            src = os.path.join(os.path.dirname(str(hook_file)), src)
        matches = sorted(_glob.glob(src)) if any(c in src for c in "*?[") else ([src] if os.path.exists(src) else [])
        for m in matches:
            p = Path(m)
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file() and "__pycache__" not in f.parts:
                        rel = f.parent.relative_to(p).as_posix()
                        out.append((str(f), os.path.normpath(os.path.join(dest, rel)).replace("\\", "/")))
            else:
                out.append((m, str(dest).replace("\\", "/") or "."))
    return out
