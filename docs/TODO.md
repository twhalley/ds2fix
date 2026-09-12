# DS2Fix — status & roadmap

## ✅ Done (v0.1.7)
- **Save content-footprint bypass** — the v1.0 blocker, solved. DS2 stamps every save with a `content_crc`
  (a hash of the installed resource set) and silently HIDES + refuses saves whose crc no longer matches the
  install; re-patching the tank or installing a data mod changes the crc, so existing saves would "disappear/
  reappear". `IsContentCrcAcceptable` (FUN_004139d0) is now forced to always accept (both the list-summary and
  load-summary readers go through it; MP content-matching is a separate path, untouched). **Proven live:** the
  Thomas party listed *and* loaded into gameplay after a real footprint change (tank crc `8a7b6adb`→`e67d647d`)
  that would otherwise have hidden it.
- **Frontend 3D preview models now render in their panels** (object_view offset, was problem #3). The
  main-menu Continue party model and the Single-Player hero-select paperdoll used to draw at the native
  800×600 top-left corner while their panels scaled to 1920; the viewport rect is now scaled with the panel
  so the model sits on its pedestal. Unblocked by two earlier fixes: wined3d (scaling no longer blanks it) and
  the footprint bypass (the frontend `dir.lqd22` recompile no longer hides the party list). Verified live on
  the main menu + Choose-Hero screen.

## ✅ Done (v0.1.6)
- Widescreen HUD anchoring, native 16:9 menus, frontend + ESC-menu scaling, configurable UI scale/res.
- All campaign difficulties unlocked; data-mod support (content CRC disabled); non-resizable window;
  version label + in-game overlay; Multiplayer button re-enabled (LAN + direct-IP).
- **wined3d render fix** — DS2's 3D `object_view` viewports (Journal cloth map, character/inventory
  paperdoll, hero-creation preview) render correctly at widescreen. (DXVK blanks them; we force wined3d.)
- gamescope no longer lingers after the game exits (supervised launcher).
- **Large-Address-Aware (2GB→4GB)** — PE-header bit, so HD-texture mods don't OOM-crash.
- **Uncapped framerate** — `maxfps` launch arg (default 120; `--maxfps 0` = uncapped).
- **Optional mod installer** — `ds2fix mods list/install/remove` + GUI section; non-bundled, SHA512-verified,
  finds the download in common folders, tracks installs for clean removal. Registered: #26 Storage Vault,
  #29 HD Textures.
- Save auto-backup + install pinning; cross-platform CLI + GUI + PyInstaller packaging; unit tests (`tests/`).

## 🟡 Open / optional
1. **Test normal co-op multiplayer** — host a game + join via **LAN** and **direct-IP** (the MP button is
   already unlocked; DS2 MP is peer-to-peer over DirectPlay8). *No dedicated server* — DS2 has no such
   concept (host-based P2P), so that idea is dropped.
2. **Re-enable the Journal → Map cloth-map scaling** — now that frontend object_view scaling is proven to
   work on wined3d, re-test the backend map targets (currently commented out in `_target_list`) and confirm
   the cloth map scales cleanly (the "out of line" symptom was the same object_view offset just fixed).
3. **Scale in-game panels** (inventory / character / spellbook / trade) — *investigated 2026-07-23.* The
   panels render at native 800×600 size, anchored top-left (functional, just small). They live in
   `character_awp.gas` (204 KB) + `character_*_tab.gas` + `gold_trade.gas`, all currently excluded from
   `_target_list` (the `in_game`/`panel` filter). Scaling is NOT the clean win it is for menus: the item
   **grid cells are fixed-size** (item icons are fixed-pixel textures the engine draws), so scaling the panel
   rect enlarges the frame but the icon grid won't follow — the classic DS2 in-game-UI-scaling wall. Needs a
   grid-aware transform, not a blanket rect scale. (Good news for testing: xdotool `i`/`j` keys DO reach
   gameplay, so panels can be driven + screenshotted.)
4. **Party leader (hero) portrait blank / mis-framed** — *NOT a Wine quirk: reproduced on native Windows 11
   + D3D9 (2026-09-11/12), and it is a STOCK DS2 bug* (DS2TroubleshootingGuide §4.1 "black portraits above
   1280 px wide"; Nexus mod #146 "High resolution Portrait Fix" targets it, with mixed reports at 1440p/Win11).
   Windows symptom: slot 1 = green frame with a **black** interior; members 2–8 fine. Wine: face pushed low
   + green clear.
   **Bisect on Windows — all negative, none of our patches is the cause:** full patch @2560x1440 and
   @1920x1080; `DS2FIX_DYNCANVAS=0` (SetScreenSize stub off); `--no-menu169` (800x600 frontend; loaded
   party); frontend grab rect mirrored to the in-game formula (`DS2FIX_PORTRAIT_RECT=1`, **newly created**
   party). Companions always fine.
   **Mechanism (static RE, 2026-09-11 workflow; code in `exe_patch.py` PATCH PORTRAIT):** a DS2 portrait is a
   64x64 **backbuffer pixel grab** taken once after an ortho render of the head (`0x5011f0`: viewport_w/h ×
   `ortho_matrix` = metres/pixel → fixed pixel size, viewport-centred), stored as texture `"portrait"` on the
   GoActor (+0x24). There are TWO generators: the **frontend** one (`RCGeneratePortrait` → `FUN_00443480`)
   grabs a rect **hard-coded for 800x600**, {380,277}-{444,341} (imm32 @0x4435ac/b3/ba/c1; pixel read
   `0x510b00`, texture `0x510900`, then `Player::SetPortrait` @0x8268f1), persisted as `portrait-0.bmp` in the
   party file and reloaded via `load://portrait-%d.bmp`. The **in-game** one (`FUN_004f25bb`) derives the rect
   from the live window ([0xbcb28c]+0xac..): x=trunc(w×0.00125×380), y=trunc(h×0.0016667×277), +64, plus fudge
   for 1280x1024 / 1024x768 / 640x480 — companions use this and look right. The hero's actor already carries a
   portrait texture after load, so the in-game lazy regen (`0x41a754`, `0x4f2e6a`; gated on +0x24 != 0) SKIPS
   it ⇒ "slot 1 wrong, 2–8 right" is **hero-vs-companion**, not slot 1. Nothing in the tank can fix it
   (`character_awp.gas` bindings/rects, `portrait_camera` — all tested or shown irrelevant; a tank-side
   `ortho_matrix` rescale only zooms the head).
   **Still open:** mirroring the frontend rect did NOT cure a new party on Windows, so the frontend pass must
   render the head elsewhere or fail the read above 1280 wide. Next candidates: (a) the frontend portrait
   pass viewport — `0x50b330(1)` / `0x513640` ("begin/end portrait pass" on renderer [0xbcb1ac]+0x24c): an
   800x600 sub-viewport or separate RT?; (b) `0x510b00` / `0x510900` — a fixed-size scratch surface or a
   1024/1280 bound (the stock ">1280 wide" threshold is the strongest clue); (c) alternative: NOP the
   frontend `Player::SetPortrait` call @0x44362a (`e8 c2 32 3e 00` → 5×`90`) so the hero stays portrait-less
   and the (working) in-game generator makes it — side effect: no leader thumbnail in the load list until the
   first in-game save.
   **Stock workaround for users:** launch at ≤1280 wide (e.g. `--res 1024x768`), create/load the party, then
   raise the resolution in the in-game Options. Cosmetic; the character is fully playable.
5. **DXVK for performance** — *re-tested 2026-07-23: DXVK v2.7.1 renders the `object_view` viewports
   correctly* (the old blank-viewport bug was specific to v2.6.2). Verified: main-menu preview, journal, and
   gameplay all render under DXVK v2.7.1 (from `GE-Proton10-34/.../dxvk/i386-windows/d3d9.dll`). The launcher
   now supports it as an **opt-in** (`DS2_RENDERER=dxvk`, or just drop a DXVK `d3d9.dll` into the game dir);
   wined3d stays the default. To promote DXVK to default, first re-verify the **cloth Map tab** under DXVK
   (the one screen not yet checked — it was the original v2.6.2 casualty).
6. **Windows end-to-end test** — *done 2026-09-11/12 on Windows 11 (GOG, Intel UHD 620, 2560x1440 @200%):*
   detection, patch/restore/idempotence, borderless launch at 1440p + centred 1080p, 16:9 menus scaled
   (1.5×/2×), previews, MP button, overlay, save list/load, gameplay HUD — all OK. Results in
   `docs/WINDOWS_TEST.md`. Not yet checked on Windows: Journal→Map, the GUI end-to-end, 4:3 modes.
7. **Gamescope fullscreen present flake** — on KDE Wayland `ds2fix play` intermittently hits "Compositor
   released us but we were not acquired" and the game bounces (teardown is clean, no lingering). Windowed
   launch is the reliable path meanwhile. Investigate gamescope flags / a windowed-fullscreen fallback.

## ⛔ Won't do / N/A
- **#27 Aranna Legacy**, **#163 HD Cutscenes** — Broken World (v2.3) only; install is base DS2.
- **#119 Enhanced UI HD** — bundles Cristi80 Resolution Fix, which conflicts with our launch-param res.
- **Dedicated MP server** — DS2 MP is peer-to-peer/host-based; no dedicated server exists.
- **Pre-rendered cutscenes** stay 4:3 (engine limit; would need re-encoding the video files).

## The "done" line
**v1.0 reached.** The two things that stood between "works" and "robust" — the save content-footprint bypass
and the frontend 3D preview offset — are both fixed and verified live. DS2 is fully playable and widescreen:
working + always-visible saves (re-patch/mod safe), native 16:9 menus with correctly-placed preview models,
journal map, LAA-backed HD-texture/storage mods, and an uncapped framerate. Everything left (#1–#5) is
optional polish or verification — nothing blocks a v1.0 tag.
