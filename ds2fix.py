#!/usr/bin/env python3
"""ds2fix — one-command patcher + launcher for Dungeon Siege II (GOG). Cross-platform (Linux/Windows).

Idempotently rebuilds the patched exe + tank from a PRISTINE backup every run (safe to re-run; never
patches an already-patched file). Then launches — fullscreen via gamescope+FSR on Linux/Wine, or a
borderless window at the monitor's resolution on Windows (alt-tab friendly; no exclusive mode switch).

  ds2fix detect                 # find + report the install
  ds2fix patch                  # patch (16:9), from pristine
  ds2fix play                   # patch + launch
  ds2fix play --res 1440x1080   # 4:3 render (pillarboxed)
  ds2fix play --no-menu169      # keep native 800x600 menu (fixes previews)
  ds2fix restore                # revert to pristine
  ds2fix info                   # show patch state
"""
import argparse, os, re, shutil, struct, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ds2fix_core import patch_exe, edit_tank, __version__

IS_WINDOWS = os.name == "nt"
EXE_NAME = "DungeonSiege2.exe"
TANK_REL = os.path.join("Resources", "Logic.ds2res")
PRISTINE_SUFFIX = ".ds2fix-pristine"


# ---------- install detection ----------
def _candidate_gamedirs():
    """Yield plausible game directories for the current OS, cheapest/most-authoritative first."""
    env = os.environ.get("DS2_GAMEDIR")
    if env:
        yield Path(env)
    yield from (_windows_dirs() if IS_WINDOWS else _linux_dirs())


# DS2's folder name differs by store (GOG vs Steam).
_DS2_DIRNAMES = ("Dungeon Siege II", "Dungeon Siege 2")


def _dirs_in(root):
    for name in _DS2_DIRNAMES:
        yield Path(root) / name


def _steam_common_dirs(steam_root):
    """Yield every steamapps/common dir for a Steam root, following libraryfolders.vdf across drives."""
    steam_root = Path(steam_root)
    yield steam_root / "steamapps" / "common"
    try:
        text = (steam_root / "steamapps" / "libraryfolders.vdf").read_text(errors="ignore")
    except OSError:
        return
    for m in re.finditer(r'"path"\s*"([^"]+)"', text):
        yield Path(m.group(1).replace("\\\\", "\\")) / "steamapps" / "common"


def _heroic_install_paths():
    """Yield install_path entries from Heroic's GOG store (the common GOG-on-Linux launcher)."""
    import json
    home = Path.home()
    for hj in (home / ".config/heroic/gog_store/installed.json",
               home / ".var/app/com.heroicgameslauncher.hgl/config/heroic/gog_store/installed.json"):
        try:
            data = json.loads(hj.read_text())
        except (OSError, ValueError):
            continue
        entries = data.get("installed", []) if isinstance(data, dict) else data
        for e in entries or []:
            ip = e.get("install_path") if isinstance(e, dict) else None
            if ip:
                yield Path(ip)


def _windows_dirs():
    import winreg, string
    # GOG: iterate every registered GOG game, yield its recorded install path (any drive/folder).
    for key in (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i); i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(k, sub) as gk:
                            yield Path(winreg.QueryValueEx(gk, "path")[0])
                    except OSError:
                        continue
        except OSError:
            continue
    # Steam: SteamPath -> libraryfolders.vdf (all libraries).
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                steam = winreg.QueryValueEx(k, "SteamPath" if hive == winreg.HKEY_CURRENT_USER else "InstallPath")[0]
            for common in _steam_common_dirs(steam):
                yield from _dirs_in(common)
        except OSError:
            continue
    # fallback: default folders on every present drive letter.
    subs = [r"GOG Games\Dungeon Siege II", r"GOG.com\Dungeon Siege II",
            r"Program Files (x86)\GOG.com\Dungeon Siege II",
            r"Program Files (x86)\Microsoft Games\Dungeon Siege II",
            r"Program Files (x86)\Steam\steamapps\common\Dungeon Siege 2"]
    for drive in (f"{c}:\\" for c in string.ascii_uppercase):
        if os.path.exists(drive):
            for sp in subs:
                yield Path(drive) / sp


def _find_prefixes(root, maxdepth=7):
    """Bounded directory walk for a wine-prefix (…/drive_c/<Games|GOG Games>/…) or Steam-library
    game dir under `root`. Prunes at drive_c/steamapps (never descends into the huge game trees) and
    caps depth, so it's safe to point at a big mount root without hanging."""
    stack = [(Path(root), 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except (OSError, PermissionError):
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if e.name == "drive_c":
                for sub in ("Games", "GOG Games", "Program Files (x86)/Microsoft Games"):
                    yield from _dirs_in(Path(e.path) / sub)
            elif e.name == "steamapps":
                yield from _dirs_in(Path(e.path) / "common")
            elif depth < maxdepth:
                stack.append((Path(e.path), depth + 1))


def _linux_dirs():
    home = Path.home()
    # explicit Wine prefix
    wp = os.environ.get("WINEPREFIX")
    if wp:
        for name in _DS2_DIRNAMES:
            for sub in ("drive_c/Games", "drive_c/GOG Games", "drive_c/Program Files (x86)/Microsoft Games"):
                yield Path(wp) / sub / name
    # Steam libraries (native + Proton), across drives via libraryfolders.vdf
    for steam_root in (home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root"):
        for common in _steam_common_dirs(steam_root):
            yield from _dirs_in(common)
    # Heroic (GOG on Linux) recorded install paths
    yield from _heroic_install_paths()
    # fallback: bounded scan of common roots (manual Wine prefixes, external drives)
    for r in (home / ".wine", home / "Games", home / ".local/share/lutris",
              Path("/run/media"), Path("/mnt"), Path("/media")):
        if r.exists():
            yield from _find_prefixes(r)


# ---------- display ----------
def _monitor_res():
    """Primary monitor's current PHYSICAL resolution. Windows: EnumDisplaySettings (independent of the
    display-scaling/DPI virtualisation a non-DPI-aware process would see). Elsewhere: 1920x1080."""
    if IS_WINDOWS:
        try:
            import ctypes
            class DEVMODEW(ctypes.Structure):
                _fields_ = [("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", ctypes.c_ushort),
                            ("dmDriverVersion", ctypes.c_ushort), ("dmSize", ctypes.c_ushort),
                            ("dmDriverExtra", ctypes.c_ushort), ("dmFields", ctypes.c_ulong),
                            ("dmPosition", ctypes.c_long * 2), ("dmDisplayOrientation", ctypes.c_ulong),
                            ("dmDisplayFixedOutput", ctypes.c_ulong), ("dmColor", ctypes.c_short),
                            ("dmDuplex", ctypes.c_short), ("dmYResolution", ctypes.c_short),
                            ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                            ("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", ctypes.c_ushort),
                            ("dmBitsPerPel", ctypes.c_ulong), ("dmPelsWidth", ctypes.c_ulong),
                            ("dmPelsHeight", ctypes.c_ulong), ("dmDisplayFlags", ctypes.c_ulong),
                            ("dmDisplayFrequency", ctypes.c_ulong), ("dmICMMethod", ctypes.c_ulong),
                            ("dmICMIntent", ctypes.c_ulong), ("dmMediaType", ctypes.c_ulong),
                            ("dmDitherType", ctypes.c_ulong), ("dmReserved1", ctypes.c_ulong),
                            ("dmReserved2", ctypes.c_ulong), ("dmPanningWidth", ctypes.c_ulong),
                            ("dmPanningHeight", ctypes.c_ulong)]
            dm = DEVMODEW(); dm.dmSize = ctypes.sizeof(DEVMODEW)
            ENUM_CURRENT_SETTINGS = -1
            if ctypes.windll.user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)) \
                    and dm.dmPelsWidth and dm.dmPelsHeight:
                return int(dm.dmPelsWidth), int(dm.dmPelsHeight)
        except Exception:  # noqa: BLE001 — any failure just falls back to the 1080p default
            pass
    return 1920, 1080


def default_res():
    """Default render resolution: the monitor's native res on Windows (borderless window fills it);
    1920x1080 on Linux (gamescope upscales to --out)."""
    return _monitor_res() if IS_WINDOWS else (1920, 1080)


def auto_scale(res_h):
    """UI scale for the 16:9 menus when none is given: 1.5 at 1080p (the tuned value), proportional
    elsewhere (1.0 @720p, 2.0 @1440p, 3.0 @2160p)."""
    return round(res_h / 720, 2)


def canvas_for(res_w, res_h, borderless):
    """The game window's CLIENT size — what the exe's dynamic UI canvas tracks and the tank transform
    centres into. Borderless (WS_POPUP): exactly the render res. Captioned window (Linux/Wine + gamescope):
    minus the frame — 1920x1080 -> 1912x1046, the measured value the transform was tuned against."""
    return (res_w, res_h) if borderless else (res_w - 8, res_h - 34)


# ---------- config / pinned install ----------
def _config_dir():
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home())
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ds2fix"


def _load_config():
    import json
    try:
        return json.loads((_config_dir() / "config.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_config(cfg):
    import json
    _config_dir().mkdir(parents=True, exist_ok=True)
    (_config_dir() / "config.json").write_text(json.dumps(cfg, indent=2))


def _pin_gamedir(path):
    """Remember this install so future runs use the SAME game + save folder (no re-detection)."""
    cfg = _load_config()
    cfg["gamedir"] = str(path)
    prefix = None if IS_WINDOWS else _wineprefix_for(Path(path))
    if prefix:
        cfg["wineprefix"] = prefix
    _save_config(cfg)


def unpin():
    cfg = _load_config()
    cfg.pop("gamedir", None); cfg.pop("wineprefix", None)
    _save_config(cfg)


def detect_gamedir(explicit=None, pin=True):
    """Resolve the DS2 install. Order: explicit (--gamedir/DS2_GAMEDIR) -> pinned config -> auto-detect.
    The resolved install is pinned so updates always launch the same game + saves (unless pin=False)."""
    if explicit:
        p = Path(explicit)
        if (p / EXE_NAME).is_file():
            if pin:
                _pin_gamedir(p)
            return p
        raise SystemExit(f"ds2fix: {EXE_NAME} not found in {p}")
    pinned = _load_config().get("gamedir")
    if pinned and (Path(pinned) / EXE_NAME).is_file():
        return Path(pinned)
    for c in _candidate_gamedirs():
        if (c / EXE_NAME).is_file():
            if pin:
                _pin_gamedir(c)
            return c
    raise SystemExit("ds2fix: could not auto-detect the DS2 install. Pass --gamedir <path> "
                     "(the folder containing DungeonSiege2.exe) or set DS2_GAMEDIR.")


# ---------- pristine backup management ----------
def _exe_is_patched(path):
    """True if the exe already carries the ds2fix section (i.e. is not pristine)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return False
    return b".ds2fix\x00" in data or b"$MSG$ds2fix" in data


def pristine_paths(gamedir):
    exe = gamedir / EXE_NAME
    tank = gamedir / TANK_REL
    return (exe.with_name(exe.name + PRISTINE_SUFFIX),
            tank.with_name(tank.name + PRISTINE_SUFFIX))


def ensure_backup(gamedir, log=print):
    """Create pristine backups of the exe + tank if they don't exist. Refuses to back up an
    already-patched exe (that would poison the pristine base)."""
    exe, tank = gamedir / EXE_NAME, gamedir / TANK_REL
    pexe, ptank = pristine_paths(gamedir)
    if not pexe.exists():
        if _exe_is_patched(exe):
            raise SystemExit(
                f"ds2fix: {exe.name} is already patched and no pristine backup exists.\n"
                "  Reinstall the game (or restore a clean exe) so ds2fix can capture a pristine base.")
        shutil.copy2(exe, pexe)
        log(f"backed up pristine exe  -> {pexe.name}")
    if not ptank.exists():
        shutil.copy2(tank, ptank)
        log(f"backed up pristine tank -> {ptank.name}")
    return pexe, ptank


# ---------- save-game backup ----------
def _windows_documents():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
            return Path(winreg.QueryValueEx(k, "Personal")[0])
    except OSError:
        return Path.home() / "Documents"


def _ds2_docs_dir(gamedir):
    """Locate the DS2 user folder (…/My Games/Dungeon Siege 2) that holds Save/ — this lives OUTSIDE the
    game dir, in the user's Documents (via a Wine symlink on Linux), and is what 'disappears' when you
    launch under a different prefix. Returns the existing folder or None."""
    rel = ("My Games", "Dungeon Siege 2")
    candidates = []
    if IS_WINDOWS:
        candidates.append(_windows_documents().joinpath(*rel))
    else:
        prefix = _wineprefix_for(gamedir)
        if prefix:
            users = Path(prefix) / "drive_c" / "users"
            if users.is_dir():
                for u in users.iterdir():
                    candidates.append(u / "Documents" / Path(*rel))
        candidates.append(Path.home() / "Documents" / Path(*rel))
    for c in candidates:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return None


def _save_backups_root():
    return _config_dir() / "save-backups"


def list_save_backups():
    root = _save_backups_root()
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if (d / "Save").is_dir())


def backup_saves(gamedir, log=print, keep=20):
    """Copy the DS2 Save/ folder to a timestamped backup under the ds2fix config dir. Returns the backup
    path (or None if there's nothing to back up). Keeps the newest `keep` backups."""
    docs = _ds2_docs_dir(gamedir)
    save = docs / "Save" if docs else None
    if not save or not save.is_dir() or not any(save.iterdir()):
        return None
    dest = _save_backups_root() / time.strftime("%Y%m%d-%H%M%S")
    if dest.exists():
        return dest   # already backed up this second
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(save, dest / "Save")
    for old in list_save_backups()[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    log(f"saves backed up -> {dest}")
    return dest


def restore_saves(gamedir, which=None, log=print):
    """Restore a save backup (latest, or a named one) into the DS2 Save/ folder. The current saves are
    themselves backed up first, so a restore is never destructive."""
    docs = _ds2_docs_dir(gamedir)
    if not docs:
        raise SystemExit("ds2fix: could not locate the DS2 save folder for this install.")
    backups = list_save_backups()
    if not backups:
        raise SystemExit("ds2fix: no save backups found yet (they're made automatically before each patch).")
    src = backups[-1] if which is None else next((b for b in backups if b.name == which), None)
    if src is None:
        raise SystemExit(f"ds2fix: no save backup named '{which}'. See `ds2fix info`.")
    backup_saves(gamedir, log)   # snapshot current before overwriting
    save = docs / "Save"
    if save.exists():
        shutil.rmtree(save)
    shutil.copytree(src / "Save", save)
    log(f"restored saves from backup {src.name} -> {save}")


# ---------- actions ----------
def do_patch(gamedir, res_w, res_h, scale, menu169, log=print, borderless=None):
    """Rebuild exe + tank from pristine. `scale=None` -> auto (see auto_scale). `borderless=None` ->
    the platform default: borderless window on Windows, captioned (gamescope-managed) window on Linux."""
    if borderless is None:
        borderless = IS_WINDOWS
    if scale is None:
        scale = auto_scale(res_h)
    canvas = canvas_for(res_w, res_h, borderless)
    backup_saves(gamedir, log)   # safety net: never lose a save to a patch/update
    pexe, ptank = ensure_backup(gamedir, log)
    exe, tank = gamedir / EXE_NAME, gamedir / TANK_REL
    log(f"patching exe (MENU_169={int(menu169)}, {res_w}x{res_h}, "
        f"{'borderless' if borderless else 'captioned'} window) ...")
    patch_exe(str(pexe), str(exe), menu169=menu169, res_w=res_w, res_h=res_h, borderless=borderless,
              canvas_w=canvas[0], canvas_h=canvas[1],
              dyncanvas=os.environ.get("DS2FIX_DYNCANVAS", "1") != "0",   # debug/bisect toggle
              portrait_rect=os.environ.get("DS2FIX_PORTRAIT_RECT", "0") == "1",   # experimental (TODO #4)
              log=lambda m: log("  " + m))
    log(f"patching tank (UI scale {scale}, canvas {canvas[0]}x{canvas[1]}) ...")
    shutil.copy2(ptank, tank)
    edit_tank(str(tank), scale=scale, backup=False, canvas=canvas, log=lambda m: log("  " + m))
    log("patch complete.")


def do_restore(gamedir, log=print):
    pexe, ptank = pristine_paths(gamedir)
    exe, tank = gamedir / EXE_NAME, gamedir / TANK_REL
    if not pexe.exists():
        raise SystemExit("ds2fix: no pristine backup found; nothing to restore.")
    shutil.copy2(pexe, exe)
    shutil.copy2(ptank, tank)
    log("restored pristine exe + tank.")


def do_info(gamedir, log=print):
    exe = gamedir / EXE_NAME
    pexe, ptank = pristine_paths(gamedir)
    pinned = _load_config().get("gamedir")
    docs = _ds2_docs_dir(gamedir)
    backups = list_save_backups()
    log(f"game dir : {gamedir}")
    log(f"pinned   : {'yes (this install is remembered across updates)' if pinned == str(gamedir) else 'no'}")
    log(f"exe      : {'PATCHED (ds2fix)' if _exe_is_patched(exe) else 'pristine/unpatched'}")
    log(f"backup   : exe={'yes' if pexe.exists() else 'no'}  tank={'yes' if ptank.exists() else 'no'}")
    log(f"saves    : {docs / 'Save' if docs else '(save folder not found)'}")
    log(f"save bkps: {len(backups)}" + (f" (latest {backups[-1].name})" if backups else ""))
    from ds2fix_core import mods as _mods
    inst = _mods.installed_mods(gamedir)
    log(f"mods     : {', '.join(inst) if inst else 'none'}")
    if IS_WINDOWS:
        mw, mh = _monitor_res()
        log(f"display  : {mw}x{mh} (default render res; borderless window)")
    log(f"platform : {'windows (borderless window, native D3D9)' if IS_WINDOWS else 'linux (wine/gamescope launch)'}")


def _wineprefix_for(gamedir):
    """Infer the Wine prefix from a GOG-under-Wine game dir (…/<prefix>/drive_c/Games/Dungeon Siege II)."""
    env = os.environ.get("WINEPREFIX")
    if env:
        return env
    p = gamedir
    for _ in range(6):
        if p.name == "drive_c":
            return str(p.parent)
        p = p.parent
    return None


def play_command(gamedir, res_w, res_h, out_w, out_h, fsr, maxfps=120):
    """Build the launch (cmd, env, note) for the current OS. Borderless window on Windows;
    gamescope+FSR (or plain windowed) via Wine on Linux. `maxfps` uncaps DS2's default 75fps limit
    (0 = fully uncapped)."""
    env = dict(os.environ)
    if IS_WINDOWS:
        # Mirrors the Linux setup (windowed game, compositor presents it fullscreen): the exe is patched
        # WS_POPUP (borderless), we launch `fullscreen=false` at the render res, and the launcher parks
        # the window at (0,0) -> borderless fullscreen when res == monitor, clean alt-tab, no exclusive
        # mode switch. (Exclusive `fullscreen=true` on native Windows ran the whole frontend at 800x600,
        # ignoring the MENU_169 patches, and can't be captured/alt-tabbed cleanly.)
        # DS2 isn't DPI-aware: at >100% display scaling Windows would bitmap-scale its window (a
        # 1920x1080 window rendering at 3840x2160 on a 200% laptop). The HighDpiAware compat layer, set
        # via the documented __COMPAT_LAYER env var (process-scoped; no registry), makes it 1:1.
        cmd = [str(gamedir / EXE_NAME), "nospacecheck=true", f"width={res_w}",
               f"height={res_h}", "fullscreen=false", "vsync=true", f"maxfps={maxfps}"]
        layers = env.get("__COMPAT_LAYER", "")
        if "highdpiaware" not in layers.lower():
            env["__COMPAT_LAYER"] = (layers + " HighDpiAware").strip()
        mw, mh = _monitor_res()
        fit = "borderless fullscreen" if (res_w, res_h) == (mw, mh) else f"borderless window, centred on {mw}x{mh}"
        return cmd, env, f"{res_w}x{res_h} {fit}"
    prefix = _wineprefix_for(gamedir)
    if prefix:
        env["WINEPREFIX"] = prefix
    env.setdefault("WINEDEBUG", "-all")
    # Renderer: default to Wine's builtin d3d9 (wined3d). Historically DXVK v2.6.2 rendered DS2's
    # [t:object_view] 3D viewports BLANK at the widescreen backbuffer (Journal cloth map, paperdoll,
    # hero preview); wined3d renders them. RE-TESTED 2026-07-23: DXVK v2.7.1 now renders those viewports
    # correctly, so DXVK is a viable opt-in for its performance. It stays OPT-IN (not the default) only
    # because the cloth Map tab under DXVK hasn't been re-verified yet. Opt in by dropping a DXVK
    # d3d9.dll into the game dir, or forcing DS2_RENDERER=dxvk (needs that dll present).
    _renderer = env.get("DS2_RENDERER", "").lower()
    _have_dxvk_dll = (gamedir / "d3d9.dll").exists()
    _use_dxvk = _renderer == "dxvk" or (_renderer != "wined3d" and _have_dxvk_dll)
    env.setdefault("WINEDLLOVERRIDES", "d3d9=n" if _use_dxvk else "d3d9=b")
    game_args = ["wine", EXE_NAME, "nospacecheck=true", f"width={res_w}",
                 f"height={res_h}", "fullscreen=false", "vsync=true", f"maxfps={maxfps}"]
    if shutil.which("gamescope"):
        gs = ["gamescope", "-W", str(out_w), "-H", str(out_h), "-w", str(res_w), "-h", str(res_h)]
        if fsr:
            gs += ["-F", "fsr"]
        gs += ["-f", "--"]
        return gs + game_args, env, f"render {res_w}x{res_h} -> {out_w}x{out_h}, gamescope{'+FSR' if fsr else ''}"
    return game_args, env, "windowed (gamescope not found)"


def _win_place_window(pid, res_w, res_h, log, timeout=90):
    """Windows: wait for the game's top-level window (class gpgwndclass_*), then MOVE it (never resize —
    DS2 doesn't rebuild its swapchain on WM_SIZE) so it is centred on the primary monitor, i.e. at (0,0)
    when the render res == the monitor (borderless fullscreen), and bring it to the front. Done from the
    launcher, so the game binary needs no positioning patch. Per-thread DPI awareness keeps the
    coordinates physical without touching the process (the Tk GUI shares it)."""
    import ctypes, ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    prev = None
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:  # noqa: BLE001 — pre-1607 Windows: coordinates stay logical, still correct at 100%
        pass
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    found = []

    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value.lower().startswith("gpgwndclass"):
                found.append(hwnd)
        return True
    t0 = time.monotonic()
    while not found and time.monotonic() - t0 < timeout:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        if not found:
            time.sleep(0.5)
    try:
        if found:
            hwnd = found[0]
            sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
            x, y = max(0, (sw - res_w) // 2), max(0, (sh - res_h) // 2)
            SWP_NOSIZE, SWP_NOZORDER = 0x0001, 0x0004
            user32.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
            user32.SetForegroundWindow(hwnd)
            log(f"window placed at {x},{y}" + (" (borderless fullscreen)" if (x, y) == (0, 0) else " (centred)"))
        else:
            log("game window not seen within the timeout — left as launched")
    finally:
        if prev:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))


def _ds2_running():
    """True if a DungeonSiege2 game process is alive (matches /proc comm, truncated to 15 chars)."""
    try:
        pids = os.listdir("/proc")
    except OSError:
        return False
    for pid in pids:
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().startswith("DungeonSiege2"):
                    return True
        except OSError:
            continue
    return False


def _teardown_gamescope(proc, prefix, log):
    """Kill the whole gamescope process tree, then wineserver — fixes gamescope lingering on Wayland."""
    import signal
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
    ws = shutil.which("wineserver")
    if ws and prefix:
        try:
            subprocess.run([ws, "-k"], env=dict(os.environ, WINEPREFIX=prefix), timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
    log("gamescope cleaned up.")


def _supervise_gamescope(proc, prefix, log):
    """Wait for DS2 to start then exit (or for gamescope to die, or a startup timeout), then tear the
    gamescope tree down. Without this, gamescope can hang around after the game quits on Wayland."""
    appeared, start = False, time.monotonic()
    try:
        while proc.poll() is None:
            if _ds2_running():
                appeared = True
            elif appeared:
                log("DS2 exited — cleaning up gamescope ...")
                break
            elif time.monotonic() - start > 120:
                log("DS2 did not start within 120s — cleaning up gamescope ...")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        log("interrupted — cleaning up gamescope ...")
    _teardown_gamescope(proc, prefix, log)


def do_play(gamedir, res_w, res_h, out_w, out_h, fsr, maxfps=120, spawn=False, log=print):
    cmd, env, note = play_command(gamedir, res_w, res_h, out_w, out_h, fsr, maxfps)
    log(f"launching ({note}) ...")
    if IS_WINDOWS:
        # Borderless window: spawn, then place the window once it exists (GUI: in the background; CLI:
        # block until the game exits, like the Linux gamescope supervisor).
        proc = subprocess.Popen(cmd, cwd=str(gamedir), env=env)
        if spawn:
            import threading
            threading.Thread(target=_win_place_window, args=(proc.pid, res_w, res_h, log), daemon=True).start()
            return proc
        _win_place_window(proc.pid, res_w, res_h, log)
        proc.wait()
        return None
    uses_gamescope = cmd and cmd[0] == "gamescope"
    if not uses_gamescope:   # plain windowed Wine — nothing to supervise
        if spawn:
            return subprocess.Popen(cmd, cwd=str(gamedir), env=env)
        os.chdir(gamedir)
        os.execvpe(cmd[0], cmd, env)
        return None
    # gamescope on Linux: run it in its own session so we can reliably tear the whole tree down,
    # then supervise so gamescope is killed when DS2 exits (Wayland-lingering fix).
    prefix = env.get("WINEPREFIX")
    proc = subprocess.Popen(cmd, cwd=str(gamedir), env=env, start_new_session=True)
    if spawn:   # GUI: supervise in the background
        import threading
        threading.Thread(target=_supervise_gamescope, args=(proc, prefix, log), daemon=True).start()
        return proc
    _supervise_gamescope(proc, prefix, log)   # CLI: block until the game exits, then clean up
    return None


# ---------- cli ----------
def _res(s):
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise argparse.ArgumentTypeError("resolution must look like 1920x1080")


def build_parser():
    p = argparse.ArgumentParser(prog="ds2fix", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"ds2fix {__version__}")
    p.add_argument("--gamedir", help="path to the folder containing DungeonSiege2.exe")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_patch_opts(sp):
        dw, dh = default_res()
        sp.add_argument("--res", type=_res, default=None, metavar="WxH",
                        help=f"render resolution (default {dw}x{dh}"
                             f"{': your monitor, borderless fullscreen' if IS_WINDOWS else ''})")
        sp.add_argument("--scale", type=float, default=None,
                        help="UI scale for the 16:9 menus (default: auto = height/720, i.e. 1.5 at 1080p)")
        sp.add_argument("--no-menu169", action="store_true",
                        help="keep the native 800x600 menu (restores the 3D model previews)")

    sub.add_parser("detect", help="find + report the install (and pin it)")
    sub.add_parser("info", help="show patch/save/pin state")
    sub.add_parser("restore", help="revert exe+tank to pristine")
    sub.add_parser("pin", help="remember this install so updates use the same game + saves")
    sub.add_parser("unpin", help="forget the pinned install (re-detect next time)")
    sub.add_parser("backup-saves", help="back up your save games now")
    sp_rs = sub.add_parser("restore-saves", help="restore saves from a backup (latest by default)")
    sp_rs.add_argument("--which", help="backup name to restore (see `ds2fix info`); default = latest")
    add_patch_opts(sub.add_parser("patch", help="patch (from pristine; auto-backs-up saves first)"))
    sp_play = sub.add_parser("play", help="patch + launch")
    add_patch_opts(sp_play)
    sp_play.add_argument("--out", type=_res, default=(2560, 1440), metavar="WxH",
                         help="monitor/output resolution for gamescope (Linux, default 2560x1440)")
    sp_play.add_argument("--no-fsr", action="store_true", help="disable FSR upscaling (Linux)")
    sp_play.add_argument("--no-patch", action="store_true", help="launch only, skip re-patching")
    sp_play.add_argument("--maxfps", type=int, default=int(os.environ.get("DS2_MAXFPS", "120")),
                         help="frame cap (DS2 defaults to 75; 0 = uncapped)")

    # optional, non-bundled mods (installed from a file you downloaded; verified by SHA512).
    sp_mods = sub.add_parser("mods", help="list/install/remove optional mods (Storage Vault, HD Textures)")
    msub = sp_mods.add_subparsers(dest="modcmd", required=True)
    msub.add_parser("list", help="show available + installed mods")
    mi = msub.add_parser("install", help="install a mod from a downloaded file")
    mi.add_argument("name", help="mod name (see `ds2fix mods list`)")
    mi.add_argument("--from", dest="src", help="path to the downloaded file (else search common folders)")
    mi.add_argument("--force", action="store_true", help="install even if SHA512 isn't in the known-good list")
    mr = msub.add_parser("remove", help="remove an installed mod")
    mr.add_argument("name", help="mod name")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "detect":
        print(detect_gamedir(args.gamedir))
        return
    if args.cmd == "unpin":
        unpin(); print("ds2fix: install unpinned (will re-detect next run)."); return
    gamedir = detect_gamedir(args.gamedir)
    if args.cmd == "info":
        do_info(gamedir)
    elif args.cmd == "pin":
        _pin_gamedir(gamedir); print(f"ds2fix: pinned install -> {gamedir}")
    elif args.cmd == "backup-saves":
        dest = backup_saves(gamedir)
        print(f"ds2fix: saves backed up -> {dest}" if dest else "ds2fix: no saves found to back up.")
    elif args.cmd == "restore-saves":
        restore_saves(gamedir, args.which)
    elif args.cmd == "restore":
        do_restore(gamedir)
    elif args.cmd == "patch":
        rw, rh = args.res or default_res()
        do_patch(gamedir, rw, rh, args.scale, not args.no_menu169)
    elif args.cmd == "play":
        rw, rh = args.res or default_res()
        if not args.no_patch:
            do_patch(gamedir, rw, rh, args.scale, not args.no_menu169)
        do_play(gamedir, rw, rh, args.out[0], args.out[1], not args.no_fsr, args.maxfps)
    elif args.cmd == "mods":
        from ds2fix_core import mods as _mods
        if args.modcmd == "list":
            _mods.print_list(gamedir)
        elif args.modcmd == "install":
            _mods.install(gamedir, args.name, src=args.src, force=args.force)
        elif args.modcmd == "remove":
            _mods.remove(gamedir, args.name)


if __name__ == "__main__":
    main()
