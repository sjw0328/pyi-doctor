"""Find what a Windows build of the app needs, using only folders on disk.

Modules are found with PyInstaller's modulegraph, pointed at the Windows
stdlib (``.pyc`` from the embeddable package), the unpacked Windows wheels
and the app folder, with Windows extension suffixes (``.pyd``) and Windows
built-in modules. Hooks run through :mod:`.hooks`. DLLs used by ``.pyd``
files are found from their PE import tables with ``pefile``.

Everything that cannot be resolved this way is collected into one
:class:`~pyi_doctor.errors.CrossBuildUnsupported` error before any output
file is written.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.machinery as im
import os
import pkgutil
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import CodeType
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from ..config import Project
from ..errors import CrossBuildUnsupported
from .hooks import EXT_SUFFIXES, HookRunner, Site, UnsupportedHook

Logger = Callable[[str], None]

# Built into python3XY.dll on Windows but not (always) on Linux/macOS.
_WIN_ONLY_BUILTINS = {"nt", "winreg", "msvcrt", "_winapi"}
_POSIX_ONLY_BUILTINS = {"posix", "pwd", "grp", "fcntl", "termios", "resource", "syslog", "readline",
                        "_posixsubprocess", "_posixshmem", "_curses", "_curses_panel", "_testinternalcapi",
                        "_xxtestfuzz", "_scproxy"}

# DLLs every supported Windows version provides (not collected when not found in the wheels).
_SYSTEM_DLL_RX = re.compile(
    r"^(api-ms-win-.*|ext-ms-.*|kernel32|kernelbase|ntdll|user32|gdi32|gdiplus|advapi32|shell32|shlwapi|ole32|"
    r"oleaut32|comctl32|comdlg32|ws2_32|wsock32|mswsock|crypt32|bcrypt|ncrypt|secur32|sspicli|version|winmm|imm32|"
    r"setupapi|cfgmgr32|iphlpapi|dnsapi|dbghelp|psapi|userenv|uxtheme|dwmapi|d3d9|d3d11|d3d12|d3dcompiler_47|dxgi|"
    r"opengl32|glu32|winhttp|wininet|urlmon|rpcrt4|msvcrt|ucrtbase|normaliz|powrprof|wtsapi32|netapi32|mpr|hid|"
    r"winspool\.drv|credui|wintrust|imagehlp|msimg32|usp10|d2d1|dwrite|mfplat|mf|mfreadwrite|avrt|ksuser|propsys|"
    r"shcore|ntmarta|winusb|setupapi|newdev|wevtapi|pdh|taskschd|wldap32|msacm32|avifil32|msvfw32|oleacc|"
    r"bthprops\.cpl|hidclass|dinput8|xinput1_4|xinput9_1_0|dsound|winscard|cryptbase|cryptsp|rstrtmgr|"
    r"comsvcs|virtdisk|fltlib|wer|dhcpcsvc|dhcpcsvc6|fwpuclnt|ndfapi|wlanapi|mprapi|rasapi32|tdh|esent|"
    r"ntdsapi|activeds|adsldpc|clusapi|resutils|authz|samlib|logoncli|netutils|srvcli|wkscli|mscms|"
    r"windowscodecs|propsys|dcomp|uiautomationcore|dbgeng|msi|cabinet|vssapi|wtsapi32|bcryptprimitives|"
    r"synchronization|powrprof|dxcore|mfcore|ninput|ondemandconnroutehelper|winhttpcom|webio)\.dll$",
    re.I,
)


@dataclass
class PureModule:
    name: str
    code: Optional[CodeType]  # None for a namespace package
    is_package: bool


@dataclass
class Collected:
    pure: Dict[str, PureModule] = field(default_factory=dict)
    base: Set[str] = field(default_factory=set)
    scripts: List[Tuple[str, CodeType]] = field(default_factory=list)  # run-time hooks first, entry script last
    binaries: Dict[str, Path] = field(default_factory=dict)  # dest (posix, relative to _internal) -> source
    datas: Dict[str, Path] = field(default_factory=dict)  # dest file (posix) -> source
    dist_infos: List[Path] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_dlls: List[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def windows_builtins(embed_dir: Path) -> Set[str]:
    pyd = {p.name.split(".")[0] for p in embed_dir.glob("*.pyd")}
    return (set(sys.builtin_module_names) - pyd - _POSIX_ONLY_BUILTINS) | _WIN_ONLY_BUILTINS


class _PkgutilShim:
    """``pkgutil`` stand-in for modulegraph: directory importers that know Windows suffixes."""

    _details = [(im.ExtensionFileLoader, EXT_SUFFIXES), (im.SourceFileLoader, [".py"]),
                (im.SourcelessFileLoader, [".pyc"])]

    def __init__(self):
        self._cache: Dict[str, im.FileFinder] = {}

    def get_importer(self, path):
        if not isinstance(path, str) or not os.path.isdir(path):
            return None
        if path not in self._cache:
            self._cache[path] = im.FileFinder(path, *self._details)
        return self._cache[path]

    def __getattr__(self, item):
        return getattr(pkgutil, item)


@contextlib.contextmanager
def _windows_lookup(builtin_names: Set[str]) -> Iterator[None]:
    from PyInstaller.lib.modulegraph import modulegraph as mg

    saved_builtins = sys.builtin_module_names
    saved_pkgutil = mg.pkgutil
    sys.builtin_module_names = tuple(sorted(builtin_names))
    mg.pkgutil = _PkgutilShim()
    try:
        yield
    finally:
        sys.builtin_module_names = saved_builtins
        mg.pkgutil = saved_pkgutil


def load_rthooks(dat_files: Sequence[Path]) -> List[Tuple[str, Path]]:
    """Read ``rthooks.dat`` files into an ordered list of (module, hook script)."""
    out: List[Tuple[str, Path]] = []
    for dat in dat_files:
        if not dat.is_file():
            continue
        mapping = ast.literal_eval(dat.read_text("utf-8"))
        for mod, scripts in mapping.items():
            for s in scripts:
                out.append((mod, dat.parent / "rthooks" / s))
    return out


def _dll_imports(path: Path) -> List[str]:
    import pefile

    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ])
        if pe.FILE_HEADER.Machine != 0x8664:
            raise CrossBuildUnsupported([f"{path.name}: not a 64-bit x64 binary (only win_amd64 is supported)"])
        names = []
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in getattr(pe, attr, []) or []:
                names.append(entry.dll.decode("ascii", "replace"))
        return names
    finally:
        pe.close()


def _within(path: Path, roots: Sequence[Path]) -> Optional[Path]:
    """The most specific root that contains ``path`` (roots may be nested)."""
    best: Optional[Path] = None
    for r in roots:
        try:
            path.relative_to(r)
        except ValueError:
            continue
        if best is None or len(r.parts) > len(best.parts):
            best = r
    return best


# ------------------------------------------------------------------ analysis
def analyze(
    project: Project,
    *,
    embed_dir: Path,
    pyi_dir: Path,
    site_dir: Path,
    log: Optional[Logger] = None,
) -> Collected:
    """Return what to bundle, or raise :class:`CrossBuildUnsupported`."""
    from PyInstaller import compat as host_compat
    from PyInstaller.lib.modulegraph import modulegraph as mg

    say = log or (lambda m: None)
    entry = project.entry_path.resolve()
    app_dir = entry.parent
    stdlib = embed_dir / "stdlib"
    fake_modules = pyi_dir / "fake-modules"
    search = [app_dir, site_dir, *_pth_dirs(site_dir), stdlib, embed_dir, fake_modules]
    roots = [p.resolve() for p in search]
    builtin_names = windows_builtins(embed_dir)
    site = Site(search, site_dir, builtin_names)

    contrib_dir = _contrib_dir()
    hook_dirs = _package_hook_dirs(site) + [pyi_dir / "hooks"]
    pre_dirs = [pyi_dir / "hooks" / "pre_safe_import_module", pyi_dir / "hooks" / "pre_find_module_path"]
    rth_files = [pyi_dir / "hooks" / "rthooks.dat"]
    if contrib_dir:
        hook_dirs.append(contrib_dir / "stdhooks")
        pre_dirs += [contrib_dir / "pre_safe_import_module", contrib_dir / "pre_find_module_path"]
        rth_files.append(contrib_dir / "rthooks.dat")
    runner = HookRunner(site, hook_dirs, pre_dirs)
    rthooks = load_rthooks(rth_files)

    out = Collected()
    problems: List[str] = []
    excludes = list(project.excludes)

    with _windows_lookup(builtin_names):
        graph = mg.ModuleGraph(path=[str(p) for p in search], excludes=excludes)
        say("Analyzing imports (Windows module layout) ...")
        root = graph.add_script(str(entry))
        script_roots = [root]

        # Modules Python needs before the PYZ importer is ready (PyInstaller's base_library.zip).
        for base in sorted(host_compat.PY3_BASE_MODULES):
            for name in site.submodules(base) or [base]:
                try:
                    for node in graph.import_hook(name, root):
                        out.base.add(node.identifier)
                except ImportError:
                    pass
        for name in project.hidden_imports:
            try:
                graph.import_hook(name, root)
            except ImportError:
                out.warnings.append(f"hidden import not found: {name}")

        processed: Set[str] = set()
        hook_excludes: Dict[str, List[str]] = {}
        added_rthooks: List[Path] = []
        rth_nodes: Dict[Path, object] = {}
        while True:
            reachable = _reachable(graph, script_roots)
            todo = [n for n in reachable if n.identifier not in processed]
            names = {n.identifier for n in reachable if not isinstance(n, (mg.MissingModule, mg.ExcludedModule))}
            new_rth = [p for mod, p in rthooks if mod in names and p not in added_rthooks]
            if not todo and not new_rth:
                break
            for node in todo:
                ident = node.identifier
                processed.add(ident)
                if isinstance(node, mg.Script) or isinstance(node, (mg.MissingModule, mg.ExcludedModule)):
                    continue
                pre = runner.pre_hook_problem(ident)
                if pre:
                    problems.append(pre)
                hook = runner.find(ident)
                if hook is not None:
                    try:
                        res = runner.run(hook)
                    except UnsupportedHook as exc:
                        problems.append(f"{ident}: PyInstaller hook {hook.name} {exc}")
                        continue
                    for h in res.hiddenimports:
                        try:
                            graph.import_hook(h, node)
                        except ImportError:
                            out.warnings.append(f"{hook.name}: hidden import not found: {h}")
                    if res.excludedimports:
                        hook_excludes[ident] = res.excludedimports
                    for src, dest in res.datas:
                        _add_file(out.datas, src, dest)
                    for src, dest in res.binaries:
                        _add_file(out.binaries, src, dest)
                # excludedimports apply to the hooked module and its submodules
                for owner, exs in hook_excludes.items():
                    if ident == owner or ident.startswith(owner + "."):
                        for target in list(graph.outgoing(node)):
                            tid = getattr(target, "identifier", None)
                            if tid and any(tid == e or tid.startswith(e + ".") for e in exs):
                                graph.removeReference(node, target)
            for p in new_rth:
                added_rthooks.append(p)
                rth_nodes[p] = graph.add_script(str(p))
                script_roots.append(rth_nodes[p])

        reachable = _reachable(graph, script_roots)

    names = {n.identifier for n in reachable}
    if names & {"tkinter", "_tkinter"} and _imported_by_app_or_required(graph, mg, reachable, app_dir, roots):
        problems.append("tkinter: the Windows embeddable Python package has no Tcl/Tk (tkinter, customtkinter, turtle)")

    # rthooks keep PyInstaller's order; the entry script runs last
    rth_order = {p: i for i, (_, p) in enumerate(rthooks)}
    for p in sorted(added_rthooks, key=lambda x: rth_order[x]):
        out.scripts.append((p.stem, rth_nodes[p].code))
    out.scripts.append((entry.stem, root.code))

    own_missing: Set[str] = set()
    for node in reachable:
        ident = node.identifier
        fn = getattr(node, "filename", None)
        if isinstance(node, (mg.Script, mg.BuiltinModule, mg.AliasNode, mg.RuntimeModule, mg.ExcludedModule)):
            continue
        if isinstance(node, mg.MissingModule):
            for importer in graph.incoming(node):
                ifn = getattr(importer, "filename", None)
                if ifn and _within(Path(ifn).resolve(), roots) == app_dir and "." not in ident:
                    own_missing.add(ident)
            continue
        if isinstance(node, (mg.InvalidSourceModule, mg.InvalidCompiledModule)):
            problems.append(f"{ident}: could not be compiled ({fn})")
            continue
        if isinstance(node, mg.Extension):
            src = Path(fn).resolve()
            base = _within(src, roots)
            if base is None:
                problems.append(f"{ident}: extension outside the Windows folders: {src}")
                continue
            dest = src.name if base == embed_dir.resolve() else src.relative_to(base).as_posix()
            out.binaries.setdefault(dest, src)
            continue
        if isinstance(node, mg.NamespacePackage):
            out.pure[ident] = PureModule(ident, None, True)
            continue
        if fn and fn != "-":
            if _within(Path(fn).resolve(), roots) is None:
                problems.append(f"{ident}: resolved outside the Windows folders: {fn}")
                continue
        code = getattr(node, "code", None)
        if code is None:
            problems.append(f"{ident}: no code object")
            continue
        is_pkg = isinstance(node, mg.Package) or (fn and os.path.basename(fn).startswith("__init__."))
        out.pure[ident] = PureModule(ident, code, bool(is_pkg))
    out.base &= set(out.pure)
    for m in sorted(own_missing):
        out.warnings.append(f"module not found in the Windows build (imported by your code): {m}")

    # Distribution metadata of bundled third-party packages (lets importlib.metadata work).
    tops = {n.split(".")[0] for n in list(out.pure) + [d.split("/")[0] for d in out.binaries]}
    for norm, d in site.dists().items():
        if any(t in tops for t in _dist_tops(d)):
            out.dist_infos.append(d)
            for f in sorted(d.rglob("*")):
                if f.is_file() and f.name != "RECORD":
                    out.datas.setdefault(f.relative_to(site_dir).as_posix(), f)

    # Project data files
    for src, dest in project.add_data:
        p = project.resolve(src)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(p).parent.as_posix()
                    _add_file(out.datas, str(f), os.path.normpath(os.path.join(dest, rel)).replace("\\", "/"))
        else:
            _add_file(out.datas, str(p), dest)

    # DLL dependencies of python3XY.dll and every collected binary.
    pydll = embed_dir / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    problems += _resolve_dlls(out, pydll, embed_dir, site_dir, app_dir)

    if problems:
        raise CrossBuildUnsupported(list(dict.fromkeys(problems)))
    return out


def _imported_by_app_or_required(graph, mg, reachable, app_dir: Path, roots: List[Path]) -> bool:
    """True if tkinter is imported outside a try/except-style optional import we can recognise.

    modulegraph does not record whether an import is optional, so this is
    conservative: tkinter imported by the app's own code, or by any package
    the app uses, counts as required unless the importer is a known optional
    integration module (for example Pillow's ImageTk).
    """
    optional_importers = {"PIL.ImageTk", "PIL.ImageShow", "matplotlib.backends", "pydoc", "turtledemo"}
    for node in reachable:
        if getattr(node, "identifier", None) not in ("tkinter", "_tkinter"):
            continue
        for importer in graph.incoming(node):
            ident = getattr(importer, "identifier", "") or ""
            if ident in ("tkinter", "_tkinter") or ident.startswith("tkinter."):
                continue
            if any(ident == o or ident.startswith(o + ".") for o in optional_importers):
                continue
            return True
    return False


def _reachable(graph, roots) -> List:
    seen: Dict[int, object] = {}
    for r in roots:
        for node in graph.flatten(start=r):
            seen.setdefault(id(node), node)
    return list(seen.values())


def _add_file(table: Dict[str, Path], src: str, dest_dir: str) -> None:
    dest_dir = (dest_dir or ".").replace("\\", "/").strip("/")
    name = Path(src).name
    dest = name if dest_dir in ("", ".") else f"{dest_dir}/{name}"
    table.setdefault(dest, Path(src).resolve())


def _dist_tops(d: Path) -> Set[str]:
    tl = d / "top_level.txt"
    if tl.is_file():
        return set(tl.read_text("utf-8", "replace").split())
    tops = set()
    rec = d / "RECORD"
    if rec.is_file():
        for line in rec.read_text("utf-8", "replace").splitlines():
            first = line.split(",", 1)[0].split("/", 1)[0]
            if first and not first.endswith((".dist-info", ".data")):
                tops.add(first.split(".", 1)[0])
    return tops


def _pth_dirs(site_dir: Path) -> List[Path]:
    """Folders that ``.pth`` files in the wheel folder add to ``sys.path`` (e.g. pywin32's ``win32``)."""
    out: List[Path] = []
    for pth in sorted(site_dir.glob("*.pth")):
        for line in pth.read_text("utf-8", "replace").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "import ", "import\t")):
                continue
            d = _ci_join(site_dir, line.replace("\\", "/"))
            if d is not None and site_dir.resolve() in d.parents and d not in out:
                out.append(d)
    return out


def _ci_join(base: Path, rel: str) -> Optional[Path]:
    """Join like Windows does: case-insensitive, ``/`` or ``\\`` separators."""
    cur = base.resolve()
    for part in [x for x in rel.split("/") if x and x != "."]:
        if part == "..":
            cur = cur.parent
            continue
        exact = cur / part
        if exact.is_dir():
            cur = exact
            continue
        match = [c for c in cur.iterdir() if c.is_dir() and c.name.lower() == part.lower()] if cur.is_dir() else []
        if not match:
            return None
        cur = match[0]
    return cur


def _contrib_dir() -> Optional[Path]:
    try:
        import _pyinstaller_hooks_contrib as contrib  # type: ignore[import-not-found]
    except ImportError:
        return None
    return Path(contrib.__file__).parent


def _package_hook_dirs(site: Site) -> List[Path]:
    """Hook folders that packages register through the ``pyinstaller40`` entry point.

    The entry point usually returns the folder of the module it names; that
    folder is used without importing anything.
    """
    out: List[Path] = []
    for d in site.dists().values():
        ep = d / "entry_points.txt"
        if not ep.is_file():
            continue
        section = None
        for line in ep.read_text("utf-8", "replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line.strip("[]").strip()
                continue
            if section == "pyinstaller40" and line.startswith("hook-dirs") and "=" in line:
                target = line.split("=", 1)[1].strip().split(":", 1)[0].strip()
                found = site.locate(target)
                if found:
                    path, is_pkg = found
                    out.append(path if is_pkg else path.parent)
    return out


def _resolve_dlls(out: Collected, pydll: Path, embed_dir: Path, site_dir: Path, app_dir: Path) -> List[str]:
    problems: List[str] = []
    index: Dict[str, List[Path]] = {}
    for f in site_dir.rglob("*.dll"):
        index.setdefault(f.name.lower(), []).append(f.resolve())
    for f in embed_dir.glob("*.dll"):
        index.setdefault(f.name.lower(), []).append(f.resolve())
    roots = [(site_dir.resolve(), False), (embed_dir.resolve(), True)]

    def dest_for(src: Path) -> str:
        for r, flat in roots:
            try:
                rel = src.relative_to(r)
            except ValueError:
                continue
            return src.name if flat else rel.as_posix()
        return src.name

    collected_names = {Path(d).name.lower() for d in out.binaries}
    queue: List[Path] = [pydll] + [p for p in out.binaries.values() if p.suffix.lower() in (".pyd", ".dll")]
    out.binaries.setdefault(pydll.name, pydll.resolve())
    seen: Set[Path] = set()
    while queue:
        b = queue.pop(0).resolve()
        if b in seen:
            continue
        seen.add(b)
        try:
            deps = _dll_imports(b)
        except CrossBuildUnsupported as exc:
            problems += exc.reasons
            continue
        except Exception as exc:  # pefile parse error
            problems.append(f"{b.name}: cannot read PE imports: {exc}")
            continue
        for dll in deps:
            low = dll.lower()
            if low in collected_names:
                continue
            cands = index.get(low, [])
            if not cands:
                if _SYSTEM_DLL_RX.match(low) or re.match(r"python3\d*\.dll$", low):
                    continue
                problems.append(f"{dll} (needed by {b.name}) is not in the Windows wheels or the embeddable Python")
                out.missing_dlls.append(dll)
                continue
            # prefer the binary's own folder, then the same top-level package, then anything
            same = [c for c in cands if c.parent == b.parent]
            top = b.relative_to(site_dir.resolve()).parts[0] if _within(b, [site_dir.resolve()]) else None
            near = [c for c in cands if top and _within(c, [site_dir.resolve() / top])]
            pick = (same or near or cands)[0]
            dest = dest_for(pick)
            if low not in collected_names:
                out.binaries.setdefault(dest, pick)
                collected_names.add(low)
                queue.append(pick)
    return problems
