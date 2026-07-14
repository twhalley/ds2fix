#!/usr/bin/env bash
# DS2Fix — one-command patch + launch for Dungeon Siege II (GOG, under Wine/Linux).
# Idempotently rebuilds the patched exe + tank from a PRISTINE base every run (so it's always
# reproducible and safe to re-run), then launches fullscreen via gamescope + FSR upscale.
#
#   ./ds2fix.sh                 # patch (16:9) + play, 1920x1080 render upscaled to 2560x1440
#   RES_W=1920 RES_H=1080 OUT_W=3840 OUT_H=2160 ./ds2fix.sh   # 4K output
#   RES_W=1440 RES_H=1080 ./ds2fix.sh                          # 4:3 render (pillarboxed by gamescope)
#   MENU_169=0 ./ds2fix.sh      # keep native 800x600 menu (HUD/canvas fix only)
#   DS2_UISCALE=1.75 ./ds2fix.sh   # scale up the (16:9) menus + in-game ESC menu (default 1.5)
#   DS2_PATCH_ONLY=1 ./ds2fix.sh   # apply patches, don't launch
#   DS2_NO_PATCH=1 ./ds2fix.sh     # launch only, skip re-patching
set -euo pipefail

# ---- config (all overridable via env) ----
GAMEDIR="${DS2_GAMEDIR:-/run/media/legion/4tb_btrfs/Games/ds2-gog/prefix/drive_c/Games/Dungeon Siege II}"
PRISTINE="${DS2_PRISTINE:-/run/media/legion/4tb_btrfs/Games/ds2-gog-backups/Dungeon Siege II.clean-base-2026-07-10}"
export WINEPREFIX="${WINEPREFIX:-/run/media/legion/4tb_btrfs/Games/ds2-gog/prefix}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="${DS2_SCRIPTS:-$SELF/patcher}"

RES_W="${RES_W:-1920}"; RES_H="${RES_H:-1080}"     # game render resolution (16:9 keeps native menu)
OUT_W="${OUT_W:-2560}"; OUT_H="${OUT_H:-1440}"     # monitor / gamescope output resolution
UISCALE="${DS2_UISCALE:-1.5}"                       # frontend upscale factor for the 16:9 canvas
export MENU_169="${MENU_169:-1}"                    # native 16:9 menu (0 = native 800x600)

# ---- locate a PRISTINE exe base (never patch a patched exe) ----
PEXE="$GAMEDIR/DungeonSiege2.exe.orig-canvas"
[ -f "$PEXE" ] || PEXE="$PRISTINE/DungeonSiege2.exe"
PTANK="$PRISTINE/Resources/Logic.ds2res"

die(){ echo "ds2fix: $*" >&2; exit 1; }
[ -d "$GAMEDIR" ]  || die "game dir not found: $GAMEDIR (set DS2_GAMEDIR)"
[ -f "$PEXE" ]     || die "pristine exe not found (set DS2_PRISTINE)"
[ -f "$PTANK" ]    || die "pristine tank not found: $PTANK"

# ---- 1. patch (idempotent: build from pristine each run) ----
if [ "${DS2_NO_PATCH:-0}" != 1 ]; then
  echo "ds2fix: patching exe (MENU_169=$MENU_169, ${RES_W}x${RES_H}) ..."
  RES_W="$RES_W" RES_H="$RES_H" python3 "$SCRIPTS/patch_dynamic.py" "$PEXE" "$GAMEDIR/DungeonSiege2.exe" \
    | sed 's/^/  /'
  echo "ds2fix: patching tank (UI scale ${UISCALE}; set DS2_UISCALE to change) ..."
  cp -f "$PTANK" "$GAMEDIR/Resources/Logic.ds2res"
  python3 "$SCRIPTS/tank_edit.py" "$GAMEDIR/Resources/Logic.ds2res" "$UISCALE" | grep -E '^OK|PATCHED' | sed 's/^/  /'
fi
[ "${DS2_PATCH_ONLY:-0}" = 1 ] && { echo "ds2fix: patch-only, done."; exit 0; }

# ---- 2. launch ----
export WINEDEBUG="${WINEDEBUG:-nostats}"
export DXVK_LOG_PATH="${DXVK_LOG_PATH:-$(dirname "$WINEPREFIX")}"
cd "$GAMEDIR"
echo "ds2fix: launching (render ${RES_W}x${RES_H} -> output ${OUT_W}x${OUT_H}, FSR) ..."
if command -v gamescope >/dev/null 2>&1; then
  exec gamescope -W "$OUT_W" -H "$OUT_H" -w "$RES_W" -h "$RES_H" -F fsr -f -- \
    wine DungeonSiege2.exe nospacecheck=true width="$RES_W" height="$RES_H" fullscreen=false vsync=true
else
  echo "ds2fix: gamescope not found — launching windowed (no upscale)" >&2
  exec wine DungeonSiege2.exe nospacecheck=true width="$RES_W" height="$RES_H" fullscreen=false vsync=true
fi
