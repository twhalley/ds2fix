#!/usr/bin/env bash
# Dungeon Siege II — GOG-under-Wine, dynamic UI-canvas patch + gamescope fullscreen upscale.
# The exe is patched so the UI canvas tracks the live backbuffer (menu 800x600, gameplay 1920x1080),
# fixing the widescreen HUD anchoring AND the off-screen menus. gamescope upscales to the monitor.
set -euo pipefail

export WINEPREFIX=/run/media/legion/4tb_btrfs/Games/ds2-gog/prefix
export WINEDEBUG=-all
export DXVK_LOG_PATH=/run/media/legion/4tb_btrfs/Games/ds2-gog
cd "$WINEPREFIX/drive_c/Games/Dungeon Siege II"

# -W/-H = monitor native (output);  -w/-h = game render res;  -F fsr = FidelityFX upscale;  -f = fullscreen.
# With the native-16:9 patch, BOTH the menu and gameplay render at 1920x1080 (16:9), so gamescope
# just clean-upscales 1920 -> 2560x1440 with no bars and no distortion anywhere (no -S stretch needed).
exec gamescope -W 2560 -H 1440 -w 1920 -h 1080 -F fsr -f -- \
  wine DungeonSiege2.exe nospacecheck=true width=1920 height=1080 fullscreen=false vsync=true
