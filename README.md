# DS2Fix

Modernizes a legally-owned **Dungeon Siege II** install (GOG, running under Wine/Linux) — widescreen
HUD, native 16:9 menus, fullscreen, and a "ds2fix" version label — via reversible binary patches and a
gamescope launcher. Does **not** distribute the game; it patches an existing install in place.

Status: **v0.1 (working)** — 16:9 menu + correct HUD + fullscreen + version label, all verified in-game.

## What works

| Feature | How |
|---|---|
| Widescreen HUD anchors correctly in gameplay | dynamic UI-canvas patch (exe) |
| Off-screen menus fixed | dynamic UI-canvas patch (exe) |
| Native 16:9 menu (not stretched) | MENU_169 patches force the frontend to render 16:9 (exe) |
| Fullscreen, upscaled to monitor | `play-ds2.sh` (gamescope + FSR) |
| Version label reads "ds2fix 0.1" | version-string patch (exe) |

## Build (apply the patches)

`patcher/patch_dynamic.py <pristine-exe> <output-exe>` produces the fully-patched exe from a **pristine**
`DungeonSiege2.exe`. Env `MENU_169=0` disables the 16:9-menu patches (leaving only the HUD/canvas fix).

```
python3 patcher/patch_dynamic.py DungeonSiege2.exe.orig DungeonSiege2.exe
```

Always keep a pristine backup of the original exe — it's the patch base and the restore point.

### What the patcher does (all in `DungeonSiege2.exe`, 32-bit PE)
- **PATCH 0 — version label:** overwrites the format string `$MSG$Version - %S` → `$MSG$ds2fix 0.1`.
- **PATCH 1 — dynamic UI canvas:** rewrites `UIShell::SetScreenSize` (`FUN_0073be90` @0x73be90) via a
  trampoline into a new `.ds2fix` PE section so the UI canvas tracks the **live** main-window rect
  (`*(0xbcb28c)`) instead of stale args → HUD + menus anchor to the real resolution.
- **PATCH 2-6 (MENU_169) — native 16:9 menu:** force the frontend window/backbuffer to 1920×1080 (the
  frontend otherwise hardcodes 800×600 through several paths): `CreateWindowExA` args @0x5f2220/0x5f2233,
  the config-read `jne`s @0x5f12f0/0x5f1344, the WorldState-transition and window-creation immediates,
  and the window-sizer choke point `FUN_005ebeba`.

Currently hardcoded to **1920×1080**. Making this configurable (4:3 / 1440p / 4K) is on the roadmap.

## Run

`./play-ds2.sh` — launches under **gamescope** fullscreen with FSR upscaling to the monitor. Edit the
`WINEPREFIX` / game path and the `-W/-H` (monitor) values for your setup.

## Tank tooling (DSg2Tank / .ds2res)

- `patcher/tank_parse.py <tank>` — parses the DSg2Tank directory (DirSet/FileSet) and reconstructs full
  logical paths for every file (e.g. finds `ui/interfaces/frontend/main_menu/main_menu.gas`).
- `patcher/tank_patch_menu.py` — in-place edits a `.gas` inside a tank (recompress + fix size/CRC/chunk
  table). **NOTE:** DS2 validates menu-file *content* beyond the tank CRC — any content change to
  `main_menu.gas` currently makes the game reject it and crash. A byte-identical recompress runs fine,
  so the repack format is correct; the content-integrity check is not yet understood. **Menu-file edits
  (button centering, hiding buttons, version-in-menu) are blocked until that's cracked.**
- `patcher/carve.py <tank> <outdir>` — zlib-carve extraction of UI `.gas` text (no full parser needed).

## Reverse-engineering (Ghidra, headless)

`ghidra/*.java` — the headless scripts used to map the resolution/UI subsystem (setter/caller discovery,
targeted decompile, xref/rect-writer scans). Run via `analyzeHeadless <proj> ds2 -process
DungeonSiege2.exe -noanalysis -scriptPath ghidra -postScript <Script>.java` (project path must be
absolute).

## Known issues
- **Menu 3D models / character avatar don't render** on the main menu (and aren't in screenshots) —
  likely the forced-1920 frontend res affecting `object_view` rendering. Under investigation.
- Menu-file edits crash the game (tank content-integrity check, see above).
- gamescope on KDE Wayland intermittently fails to present ("Compositor released us but we were not
  acquired") — usually resolves on alt-tab / relaunch.

## Roadmap (backlog)
- Configurable resolution (4:3 / 16:9 / 4K)
- Top-right "ds2fix — injected & running" overlay (MangoHud)
- Spacebar to skip cutscenes/NIS
- F1/F2/F3 weapon/spell loadout switching
- Auto-unlock all campaign difficulties
- Fix menu model rendering
- One-click launcher/patcher with backup/restore

---
🤖 Tooling built with [Claude Code](https://claude.com/claude-code)
