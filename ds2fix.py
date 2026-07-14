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
import argparse, os, shutil, struct, subprocess, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ds2fix_core import patch_exe, edit_tank, __version__

IS_WINDOWS = os.name == "nt"
EXE_NAME = "DungeonSiege2.exe"
TANK_REL = os.path.join("Resources", "Logic.ds2res")
PRISTINE_SUFFIX = ".ds2fix-pristine"


# ---------- install detection ----------
def _candidate_gamedirs():
    """Yield plausible game directories for the current OS."""
    env = os.environ.get("DS2_GAMEDIR")
    if env:
        yield Path(env)
    if IS_WINDOWS:
        roots = [
            r"C:\GOG Games\Dungeon Siege II",
            r"C:\Program Files (x86)\GOG.com\Dungeon Siege II",
            r"C:\Program Files (x86)\Steam\steamapps\common\Dungeon Siege 2",
            r"C:\Program Files (x86)\Microsoft Games\Dungeon Siege II",
        ]
        for r in roots:
            yield Path(r)
    else:
        home = Path.home()
        # common Wine/Proton/GOG-under-Wine layouts
        globs = [
            "**/drive_c/Games/Dungeon Siege II",
            "**/drive_c/GOG Games/Dungeon Siege II",
            "**/steamapps/common/Dungeon Siege 2",
        ]
        search_roots = [home / ".wine", home / "Games", Path("/run/media"),
                        home / ".local/share/Steam", home / ".steam"]
        for sr in search_roots:
            if not sr.exists():
                continue
            for g in globs:
                try:
                    for hit in sr.glob(g):
                        yield hit
                except (OSError, PermissionError):
                    continue


def detect_gamedir(explicit=None):
    if explicit:
        p = Path(explicit)
        if (p / EXE_NAME).is_file():
            return p
        raise SystemExit(f"ds2fix: {EXE_NAME} not found in {p}")
    for c in _candidate_gamedirs():
        if (c / EXE_NAME).is_file():
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


# ---------- actions ----------
def do_patch(gamedir, res_w, res_h, scale, menu169, log=print):
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
    log(f"game dir : {gamedir}")
    log(f"exe      : {'PATCHED (ds2fix)' if _exe_is_patched(exe) else 'pristine/unpatched'}")
    log(f"backup   : exe={'yes' if pexe.exists() else 'no'}  tank={'yes' if ptank.exists() else 'no'}")
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


def do_play(gamedir, res_w, res_h, out_w, out_h, fsr, spawn=False, log=print):
    cmd, env, note = play_command(gamedir, res_w, res_h, out_w, out_h, fsr)
    log(f"launching ({note}) ...")
    if spawn:   # GUI: don't replace our process — spawn the game and keep running
        return subprocess.Popen(cmd, cwd=str(gamedir), env=env)
    os.chdir(gamedir)
    os.execvpe(cmd[0], cmd, env)


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

    sub.add_parser("detect", help="find + report the install")
    sub.add_parser("info", help="show patch state")
    sub.add_parser("restore", help="revert to pristine")
    add_patch_opts(sub.add_parser("patch", help="patch (from pristine)"))
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
    gamedir = detect_gamedir(args.gamedir)
    if args.cmd == "info":
        do_info(gamedir)
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
