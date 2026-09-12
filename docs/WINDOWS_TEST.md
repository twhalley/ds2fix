# ds2fix — Windows end-to-end test checklist

Goal: verify ds2fix patches + launches Dungeon Siege II correctly on **native Windows** (the patcher
core is cross-platform; this confirms the Windows-specific paths — registry install detection, the
borderless-window launch, `%APPDATA%` config/save-backup — plus the one thing Wine can't tell us: whether the
**party leader's portrait** renders correctly on native D3D9).

> **Run 2026-09-11/12** — Windows 11 Pro 26200, GOG DS2 v2.3 in `C:\GOG Games\Dungeon Siege 2`, Intel UHD 620,
> 2560x1440 at 200% display scaling, ds2fix 0.1.8 from source (`python ds2fix.py …`) and PyInstaller
> `ds2fix.exe` / `ds2fix-gui.exe` built locally. Results are marked ✅ / ❌ / ⬜ (not run) below.

## 0. Prerequisites
- A Windows 10/11 machine.
- Dungeon Siege II installed (GOG or Steam; base DS2, matching the Linux target). Note the folder that
  contains `DungeonSiege2.exe`.
- The build: download `ds2fix.exe` (CLI) and `ds2fix-gui.exe` (GUI) from the latest **GitHub Release**
  (https://github.com/twhalley/ds2fix/releases). No Python needed.

## 1. Detection (registry auto-detect)
```
ds2fix.exe detect
```
- [x] ✅ Prints the correct game folder (the one with `DungeonSiege2.exe`) — found via the GOG registry key.
- If it can't find it: `ds2fix.exe --gamedir "C:\GOG Games\Dungeon Siege II" detect` (or set
  `DS2_GAMEDIR`). Detection walks HKLM `GOG.com\Games`, Steam `libraryfolders.vdf`, then default folders.

## 2. State / info
```
ds2fix.exe info
```
- [x] ✅ `exe: pristine/unpatched`, `platform: windows (borderless window, native D3D9)`, a `display:` line
      with the monitor res, and `saves:` at `...\Documents\My Games\Dungeon Siege 2\Save` (once the game has
      created it — a never-run install shows `(save folder not found)`).

## 3. Patch (from pristine)
```
ds2fix.exe patch
```
- [x] ✅ Backs up saves, writes `DungeonSiege2.exe.ds2fix-pristine` + `Logic.ds2res.ds2fix-pristine`,
      patches exe + tank, ends `patch complete.` (every exe patch site matched the GOG Windows exe).
- [x] ✅ `ds2fix.exe info` now shows `exe: PATCHED (ds2fix)`.
- [x] ✅ Re-running `patch` is idempotent (rebuilds from pristine, never double-patches).

## 4. Launch + in-game verification
```
ds2fix.exe play
```
(`play` re-patches from pristine then launches a **borderless window at your monitor's resolution** —
borderless fullscreen, clean alt-tab. `--res 1920x1080` renders smaller, centred; `--no-patch` launches only.
Exclusive `fullscreen=true` is deliberately NOT used: on native Windows it ran the whole frontend at 800x600.)

Verify in-game:
- [x] ✅ Launches as a **borderless window at (0,0), client == render res** (2560x1440 and 1920x1080 tested;
      1080p is centred on the 1440p monitor). DPI-aware (no bitmap scaling at 200%).
- [x] ✅ Main menu is **16:9**, scaled 2× at 1440p / 1.5× at 1080p and centred; the **Continue preview model**
      and the create-hero paperdoll sit in their panels; all three difficulties selectable.
- [x] ✅ **Multiplayer** button is enabled; in-game overlay reads `ds2fix 0.1.8`.
- [x] ✅ **Saves list and load** — a party created in one run appears and loads in the next.
- [ ] ⬜ Journal → **Map** (cloth map) — not driven on Windows yet.
- [x] ❌ ⭐ **Party leader portrait (member 1):** green frame with a **black** interior on native D3D9 at
      1920x1080 and 2560x1440 (members 2+ fine). So it is NOT a Wine quirk — it is the stock DS2
      "black portrait above 1280 wide" bug. Bisected: none of the ds2fix patches cause it. Full analysis +
      next steps in `docs/TODO.md` item 4.

## 5. Saves safety + restore
- [x] ✅ `ds2fix.exe info` lists save backups once a Save folder exists (auto-made before each patch).
- [x] ✅ `ds2fix.exe restore` reverts exe + tank to pristine; `info` shows `pristine/unpatched` again.

## 6. GUI smoke test
- [ ] ⬜ `ds2fix-gui.exe` builds (PyInstaller, 10 MB) and its defaults follow the monitor res / auto scale;
      the click-through (detect → Patch + Play) has not been driven on Windows yet.

## What to report back
- Any command that errors (copy the message).
- The member-1 portrait result (#4) — the key unknown.
- Anything that behaves differently from the Linux/Wine build.
