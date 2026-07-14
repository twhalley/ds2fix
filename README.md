# DS2Fix

Modernizes a legally-owned **Dungeon Siege II** install (GOG, running under Wine/Linux) — widescreen
HUD, native 16:9 menus, fullscreen, campaign difficulties unlocked, and a "ds2fix" version label — via
reversible binary patches + data-side tank mods and a gamescope launcher. Does **not** distribute the
game; it patches an existing install in place, always from a pristine base.

Status: **v0.1 (working)** — everything below verified in-game.

## What works

| Feature | How |
|---|---|
| Widescreen HUD anchors correctly in gameplay | dynamic UI-canvas patch (exe) |
| Off-screen menus fixed | dynamic UI-canvas patch (exe) |
| Native 16:9 menu (not stretched) | `MENU_169` patches force the frontend to render 16:9 (exe) |
| Frontend menus scaled + centered for 16:9 | tank `.gas` rect transform (`tank_edit.py`) |
| In-game (ESC/pause) menu scaled + centered for 16:9 | tank `.gas` rect transform (`tank_edit.py`) |
| In-game "ds2fix 0.1" overlay, top-right during gameplay | tank `.gas` text node injected into `data_bar` HUD |
| All campaign difficulties (Merc/Vet/Elite) unlocked from the start | completion-check patch (exe) |
| Tank data mods no longer crash the game | content-integrity CRC check disabled (exe) |
| Non-resizable window (no resize black-screen) | window-style patch (exe) |
| Configurable render + output resolution | `RES_W/RES_H`, `OUT_W/OUT_H` env |
| Configurable UI scale (menus + ESC menu) | `DS2_UISCALE` env (default 1.5) |
| Fullscreen, upscaled to monitor | `ds2fix.sh` / `play-ds2.sh` (gamescope + FSR) |
| Version label reads "ds2fix 0.1" | version-string patch (exe) |

## Quick start

```
./ds2fix.sh                                   # patch (16:9) + play, 1920x1080 -> 2560x1440 (FSR)
OUT_W=3840 OUT_H=2160 ./ds2fix.sh             # 4K output
RES_W=1440 RES_H=1080 ./ds2fix.sh             # 4:3 render (gamescope pillarboxes)
MENU_169=0 ./ds2fix.sh                        # native 800x600 menu (HUD/canvas fix only)
DS2_UISCALE=1.75 ./ds2fix.sh                  # bigger menus + ESC menu (default 1.5)
DS2_PATCH_ONLY=1 ./ds2fix.sh                  # apply patches, don't launch
```

`ds2fix.sh` **idempotently rebuilds** the patched exe + tank from a pristine base every run (safe to
re-run; never patches an already-patched file). Override paths with `DS2_GAMEDIR` / `DS2_PRISTINE`.

## The exe patcher (`patcher/patch_dynamic.py <pristine-exe> <out-exe>`)

All patches are on the pristine 32-bit `DungeonSiege2.exe`; keep a pristine backup as the patch base.
Env: `MENU_169` (0 disables the 16:9 menu), `RES_W`/`RES_H` (forced frontend res, default 1920×1080),
`CHOKE`/`WS169` (fine-grained MENU_169 sub-toggles).

- **Version label** — `$MSG$Version - %S` → `$MSG$ds2fix 0.1`.
- **Dynamic UI canvas** — `UIShell::SetScreenSize` (`FUN_0073be90`) trampolined through a new `.ds2fix`
  PE section so the canvas tracks the **live** window rect (`*(0xbcb28c)`) → HUD + menus anchor correctly.
- **Content-integrity CRC disable** — `FUN_00699df1` → `return 1` (+ secondary verifier). The key
  enabler: DS2 CRC-checks loaded `.gas` content (GameSpy-era anti-cheat); without this, any tank data
  mod crashes. Disabling it unlocks all data-side mods.
- **Auto-unlock difficulties** — `FUN_004171d7` (the `<world>_completed_<difficulty>` journal check,
  used by both SP `CanStartWorld` and MP join) forced to "always completed" → Veteran + Elite selectable
  from the start.
- **Non-resizable window** — drop `WS_THICKFRAME` (`0x10ce0000`→`0x10ca0000`); DS2 never rebuilds the
  swapchain on `WM_SIZE`, so dragging the border used to black it out.
- **MENU_169 (native 16:9 menu)** — force the frontend window/backbuffer to `RES_W×RES_H` (it otherwise
  hardcodes 800×600 through several paths): `CreateWindowExA` args, the config-read `jne`s, the
  WorldState/window-creation immediates, and the window-sizer choke point `FUN_005ebeba`.

## Tank tooling (DSg2Tank / .ds2res)

- **`patcher/tank_edit.py <tank> [scale]`** — general in-place `.gas` editor. Scales+centers the frontend
  **and in-game (ESC) menus** into the 16:9 canvas (each about its own content centre, so the 640×480 ESC
  menu lands centred like the 800×600 frontend), **and injects the top-right "ds2fix 0.1" overlay text node
  into the always-on `data_bar` HUD**. Recompresses within each file's slot, fixes size/CRC/chunk-table, and
  bumps the `.gas` FILETIME past its compiled `dir.lqd22` cache so the engine recompiles from source. To fit
  tight slots it strips trailing whitespace / blank lines (and dedents skrit-free files). Handles
  **multi-chunk** files correctly (each 16384-byte block = `zlib(first 16368B)` + 16 raw content bytes;
  table = `[total, blocksize] + per-chunk[uncomp, comp, rawtail, reloff]`, 4-byte aligned after the name).
  Requires the CRC check disabled (above).
- `patcher/tank_parse.py <tank>` — parse the DirSet/FileSet and reconstruct logical file paths.
- `patcher/carve.py <tank> <outdir>` — zlib-carve extraction of UI `.gas` text.

## Reverse-engineering (Ghidra, headless)

`ghidra/*.java` — headless scripts used to map the subsystems. `XChase.java` (xref+decompile via
`DS2_ADDRS`/`DS2_OUT`) and `Decomp.java` (decompile via `DS2_TARGETS`) are the workhorses. Run via
`analyzeHeadless <proj> ds2 -process DungeonSiege2.exe -noanalysis -scriptPath ghidra -postScript X.java`.

## Known issues
- **`object_view` 3D model previews render only at an 800×600 backbuffer** — the char-select / create-hero
  model, the inventory/character paperdoll, and the menu party preview are blank at higher resolutions.
  World models (party, enemies) render fine; this is specific to the UI preview panels, and character
  creation is fully functional by name/stats. **Diagnosis (extensive):** the `object_view` UI *instance* is
  identical at 800 vs 1920 (verified by live-memory A/B diff — same rect, same `+0x78/+0x7c` dims) and the 3D
  *scene* renders (the background fills the frame); only the character **actor is never submitted to the
  scene draw** at high res. It is *not* the 2D UI-element cull (`FUN_0075dbc0` — NOP-tested, no effect) nor a
  stored instance field. Fixing it needs tracing the actor-submission render call (apitrace D3D-call diff, or
  a working debugger under non-wow64 wine — this system's wine 11 is wow64-only, so breakpoints don't fire).
  Workaround: run the frontend at native 800×600 (`MENU_169=0`) + gamescope upscale to get the previews back.
- gamescope on KDE Wayland intermittently fails to present ("Compositor released us but we were not
  acquired") — usually resolves on alt-tab / relaunch.

## Roadmap (backlog)
- Windows-compatible packaging (patcher is Python/cross-platform; launcher is Linux/gamescope)
- `object_view` model previews at high res (needs render-call tracing — see Known issues)
- Co-op multiplayer revival (direct-IP/LAN over VPN; GameSpy master-server replacement) — GameSpy is dead

---
🤖 Tooling built with [Claude Code](https://claude.com/claude-code)
