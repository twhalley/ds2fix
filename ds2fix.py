#!/usr/bin/env python3
"""ds2fix — one-command patcher + launcher for Dungeon Siege II (GOG). Cross-platform (Linux/Windows).

Idempotently rebuilds the patched exe + tank from a PRISTINE backup every run (safe to re-run; never
patches an already-patched file). Then launches — fullscreen via gamescope+FSR on Linux/Wine, or native
fullscreen on Windows.

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
def do_patch(gamedir, res_w, res_h, scale, menu169, log=print):
    backup_saves(gamedir, log)   # safety net: never lose a save to a patch/update
    pexe, ptank = ensure_backup(gamedir, log)
    exe, tank = gamedir / EXE_NAME, gamedir / TANK_REL
    log(f"patching exe (MENU_169={int(menu169)}, {res_w}x{res_h}) ...")
    patch_exe(str(pexe), str(exe), menu169=menu169, res_w=res_w, res_h=res_h,
              log=lambda m: log("  " + m))
    log(f"patching tank (UI scale {scale}) ...")
    shutil.copy2(ptank, tank)
    edit_tank(str(tank), scale=scale, backup=False, log=lambda m: log("  " + m))
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
    log(f"platform : {'windows (native launch)' if IS_WINDOWS else 'linux (wine/gamescope launch)'}")


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


def play_command(gamedir, res_w, res_h, out_w, out_h, fsr):
    """Build the launch (cmd, env, note) for the current OS. Native fullscreen on Windows;
    gamescope+FSR (or plain windowed) via Wine on Linux."""
    env = dict(os.environ)
    if IS_WINDOWS:
        cmd = [str(gamedir / EXE_NAME), "nospacecheck=true", f"width={res_w}",
               f"height={res_h}", "fullscreen=true", "vsync=true"]
        return cmd, env, "native fullscreen"
    prefix = _wineprefix_for(gamedir)
    if prefix:
        env["WINEPREFIX"] = prefix
    env.setdefault("WINEDEBUG", "-all")
    game_args = ["wine", EXE_NAME, "nospacecheck=true", f"width={res_w}",
                 f"height={res_h}", "fullscreen=false", "vsync=true"]
    if shutil.which("gamescope"):
        gs = ["gamescope", "-W", str(out_w), "-H", str(out_h), "-w", str(res_w), "-h", str(res_h)]
        if fsr:
            gs += ["-F", "fsr"]
        gs += ["-f", "--"]
        return gs + game_args, env, f"render {res_w}x{res_h} -> {out_w}x{out_h}, gamescope{'+FSR' if fsr else ''}"
    return game_args, env, "windowed (gamescope not found)"


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


def do_play(gamedir, res_w, res_h, out_w, out_h, fsr, spawn=False, log=print):
    cmd, env, note = play_command(gamedir, res_w, res_h, out_w, out_h, fsr)
    log(f"launching ({note}) ...")
    uses_gamescope = (not IS_WINDOWS) and cmd and cmd[0] == "gamescope"
    if not uses_gamescope:   # native (Windows) / plain windowed — nothing to supervise
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
        sp.add_argument("--res", type=_res, default=(1920, 1080),
                        metavar="WxH", help="render resolution (default 1920x1080)")
        sp.add_argument("--scale", type=float, default=1.5, help="UI scale for the 16:9 menus (default 1.5)")
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
        do_patch(gamedir, args.res[0], args.res[1], args.scale, not args.no_menu169)
    elif args.cmd == "play":
        if not args.no_patch:
            do_patch(gamedir, args.res[0], args.res[1], args.scale, not args.no_menu169)
        do_play(gamedir, args.res[0], args.res[1], args.out[0], args.out[1], not args.no_fsr)


if __name__ == "__main__":
    main()
