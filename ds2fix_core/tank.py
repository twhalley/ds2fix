#!/usr/bin/env python3
# DSg2Tank .gas editor (importable core). Same logic as tank_edit.py, parameterised. Output is
# byte-identical to the script for the same scale. Scales+centers the frontend & in-game ESC menus into
# the 16:9 canvas and injects the top-right "ds2fix 0.1" overlay into the always-on data_bar HUD.
# Requires the exe CRC content-check disabled (see exe_patch.py).
import struct, zlib, re, shutil
try:
    from ._version import __version__            # imported as a package (normal)
except ImportError:
    from _version import __version__             # run directly as a script

# Default UI canvas = the CLIENT area of the 1920x1080 game window on Linux/Wine (a captioned window:
# 1920-8 x 1080-34). The exe's dynamic-canvas patch makes the engine's UI canvas track the live window
# rect, so the tank transform must centre into the SAME size. Pass `canvas=(w,h)` to override — the CLI
# derives it from --res and the window style (borderless on Windows -> canvas == res exactly).
CW, CH = 1912, 1046
# text_version "ds2fix 0.1" -> top-right corner, anchored to the canvas' right edge.
OVERRIDES = { (1, 576, 284, 599): lambda cw, ch: (cw - 322, 12, cw - 12, 40) }

TARGETS = [
    'ui/interfaces/frontend/main_menu/main_menu.gas',
    'ui/interfaces/frontend/frontend_help/frontend_help.gas',
    'ui/interfaces/frontend/create_party/create_party.gas',        # multi-chunk (17504B / 2 chunks)
    'ui/interfaces/frontend/difficulty_menu/difficulty_menu.gas',   # Easy/Normal/Hard selector
    'ui/interfaces/frontend/load_game/load_game.gas',               # campaign "Select Difficulty"
    'ui/interfaces/backend/in_game_menu/in_game_menu.gas',          # in-game ESC/pause menu (640x480)
    'ui/interfaces/frontend/multiplayer_provider/multiplayer_provider.gas',  # Select MP Connection
]

# Per-interface authoring-canvas override for centering (default 800x600 works for everything so far).
INTENDED = {}

# Backend map/journal screens authored in the 800x600 canvas. They were never ported to widescreen, so at
# 1912x1046 they render crammed in the top-left ~42% (Journal -> Map sits in a corner, hard to see). We
# scale+center the WHOLE journal (frame + every book + every page: it's one coherent UI, so scaling only the
# Map tab would misalign it against the shared frame/tabs) plus the teleporter maps. For these, the map is
# drawn by an [t:object_view] whose rect IS scaled here (scale_object_view) so the cloth-map viewport grows
# with the frame. All BACKEND -> their dir.lqd22 recompiles only in-game -> party-list-safe (bug-1 is the
# recompile of FRONTEND interfaces). Full survey + remaining tiers: docs/MAP_PORTING_TODO.md.
MAP_PREFIXES = ('ui/interfaces/backend/journal/',)          # frame + all books + all pages
MAP_FILES = (
    'ui/interfaces/backend/teleport/teleport.gas',          # teleporter destination map
    'ui/interfaces/backend/quest_teleporter/quest_teleporter.gas',  # quest teleporter map
    'ui/interfaces/backend/drawn_map/drawn_map.gas',        # full-screen M-key world map (fill)
    'ui/interfaces/backend/radar_overlay/radar_overlay.gas',  # its border frame overlay (fill)
)
# The full-screen map's [t:object_view] is authored at 0,0,800,600 (fills the whole canvas), so its viewport
# is STRETCHED to the full 1912x1046 client instead of center-scaled (which would leave it a box mid-screen).
# The surrounding chrome (close button, compass) still center-scales normally.
MAP_FILL_TARGETS = ('ui/interfaces/backend/drawn_map/drawn_map.gas',)

def _is_map_target(path):
    return any(path.startswith(p) for p in MAP_PREFIXES) or path in MAP_FILES

# The "Select Multiplayer Connection" menu — the exe patch re-enables the Multiplayer button; GameSpy is a
# dead service (and the RCE surface), so hide + neuter its option here (leaving LAN + Internet/direct-IP).
MP_PROVIDER = 'ui/interfaces/frontend/multiplayer_provider/multiplayer_provider.gas'

def customize_mp_provider(u):
    off = b'rect = -9000,-9000,-8999,-8999;'                        # far off-screen (text renders even at 0-size)
    u = u.replace(b'rect = 295,353,503,399;', off)                  # button_gamespy
    u = u.replace(b'rect = 365,365,503,399;', off)                  # its "GameSpy" label
    u = u.replace(b'UIFrontend.TransitionToMPGamespy();', b'')       # neuter the click, just in case
    # gamespy is hidden -> blank its description (also reclaims tank-slot space for the text below)
    u = u.replace(b'Log in to GameSpy and play Dungeon Siege II with other GameSpy members.\\n\\n'
                  b'Uses parties stored online in the GameSpy Vault.', b'')
    # inject practical how-to-host info into the Internet (direct-IP) description. DS2 = DirectPlay8,
    # so the ports are UDP 2302 (session) + 6073 (DPNSVR enumeration).
    u = u.replace(
        b'Play Dungeon Siege II anonymously over the Internet.\\n\\nUses parties stored on your computer.',
        b'Host or join over the Internet by IP address.\\n\\n'
        b'HOST: forward UDP 2302 + 6073, then share your IP.\\n'
        b'JOIN: enter the host IP.\\n\\n'
        b'Tip: a VPN (Tailscale/ZeroTier) needs no port forwarding.')
    return u

# In-game "ds2fix 0.1" overlay: injected into the always-on data_bar HUD (needs `visible = true`).
OVERLAY_TARGET = 'ui/interfaces/backend/data_bar/data_bar.gas'
OVERLAY_ANCHOR = b'\t[t:button,n:button_collect_loot_bg]'


def _overlay_node(version):
    """Build the top-right overlay text node for the given version (e.g. 'ds2fix 0.1.2')."""
    return (b'\t[t:text,n:text_ds2fix]\r\n'
            b'\t{\r\n'
            b'\t  x font_color = -1;\r\n'
            b'\t  i draw_order = 200;\r\n'
            b'\t\tfont_type = b_gui_fnt_16p_ringbearer-gold;\r\n'
            b'\t  b is_right_anchor = true;\r\n'
            b'\t\tjustify = right;\r\n'
            b'\t\trect = 640,4,797,28;\r\n'
            b'\t  i right_anchor = 160;\r\n'
            b'\t\ttext = "ds2fix ' + version.encode('latin1') + b'";\r\n'
            b'\t  b topmost = true;\r\n'
            b'\t  b visible = true;\r\n'
            b'\t}\r\n')


if OVERLAY_TARGET not in TARGETS:
    TARGETS.append(OVERLAY_TARGET)

BLK = 0x4000  # 16384-byte uncompressed block per zlib chunk


def scale_center(u, scale, iw=800, ih=600, scale_object_view=False, fill_object_view=False, canvas=None):
    cw, ch = canvas or (CW, CH)
    ox = (cw - iw*scale) / 2      # centre the intended canvas (iw x ih) inside the cw x ch output
    oy = (ch - ih*scale) / 2
    def repl(m):
        x1,y1,x2,y2 = (int(v) for v in m.group(1,2,3,4))
        if (x1,y1,x2,y2) in OVERRIDES:
            return b'rect = %d,%d,%d,%d' % OVERRIDES[(x1,y1,x2,y2)](cw, ch)
        return b'rect = %d,%d,%d,%d' % (round(ox+x1*scale), round(oy+y1*scale),
                                        round(ox+x2*scale), round(oy+y2*scale))
    # An [t:object_view] is a live 3D/cloth viewport (a model preview OR the journal/teleport map). Whether
    # its rect may be scaled depends on WHICH object_view:
    #  * FRONTEND hero/party preview (scale_object_view=False, the default): leave the rect EXACTLY as
    #    authored. The preview renders only at an 800x600 backbuffer (hero-preview res gate); moving/resizing
    #    the rect gained nothing and muddied the create_party boot path, so keep it verbatim. Cost: the
    #    preview sits at its 800x600 spot while its panel scales away — cosmetic.
    #  * BACKEND map screens (scale_object_view=True): the map viewport SHOULD grow with the book frame,
    #    else the Journal -> Map cloth map stays crammed in the top-left corner while its frame scales around
    #    it. These are backend, so recompiling them can't touch frontend party enumeration (bug-1 is the
    #    dir.lqd22 recompile of FRONTEND interfaces, not a rect edit). See docs/MAP_PORTING_TODO.md.
    out, elem = [], None
    for line in u.split(b'\n'):
        m = re.match(rb'\s*\[t:([^,\]]+)', line)
        if m:
            elem = m.group(1).strip()
        if elem == b'object_view' and fill_object_view:
            out.append(re.sub(rb'rect = -?\d+,\s*-?\d+,\s*-?\d+,\s*-?\d+',
                              b'rect = 0,0,%d,%d' % (cw, ch), line))   # full-screen map fills the client
        elif elem == b'object_view' and not scale_object_view:
            out.append(line)                      # verbatim: never touch a frontend preview viewport
        else:
            out.append(re.sub(rb'rect = (-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)', repl, line))
    u = b'\n'.join(out)
    u = re.sub(rb'[ \t]+(\r?\n)', rb'\1', u)          # trailing whitespace
    u = re.sub(rb'(?m)^[ \t]*\r?\n', b'', u)           # blank lines
    if b'[[' not in u:                                 # dedent skrit-free files to reclaim slot bytes
        u = re.sub(rb'(?m)^[ \t]+', b'', u)
    return u


def insert_overlay(u, version):
    if b'text_ds2fix' in u:   # idempotent
        return u
    i = u.find(OVERLAY_ANCHOR)
    assert i != -1, "data_bar overlay anchor element not found"
    u = u[:i] + _overlay_node(version) + u[i:]
    u = re.sub(rb'[ \t]+(\r?\n)', rb'\1', u)
    u = re.sub(rb'(?m)^[ \t]*\r?\n', b'', u)
    return u


def parse(d):
    ds=struct.unpack('<I',d[0x0c:0x10])[0]; fs=struct.unpack('<I',d[0x10:0x14])[0]
    dc=struct.unpack('<I',d[ds:ds+4])[0]; do=struct.unpack('<%dI'%dc,d[ds+4:ds+4+4*dc])
    dirs={}
    for eo in do:
        p=ds+eo; nl=struct.unpack('<H',d[p+16:p+18])[0]
        dirs[eo]=(struct.unpack('<I',d[p:p+4])[0], d[p+18:p+18+nl].split(b'\0')[0].decode('latin1'))
    def dp(eo):
        r=[];s=set()
        while eo and eo in dirs and eo not in s:
            s.add(eo); pa,nm=dirs[eo]
            if nm: r.append(nm)
            eo=pa
        return '/'.join(reversed(r))
    fc=struct.unpack('<I',d[fs:fs+4])[0]; fo=struct.unpack('<%dI'%fc,d[fs+4:fs+4+4*fc])
    files={}; offs=[]
    for eo in fo:
        p=fs+eo; parent,size,dataoff,crc=struct.unpack('<IIII',d[p:p+16]); nl=struct.unpack('<H',d[p+28:p+30])[0]
        name=d[p+30:p+30+nl].split(b'\0')[0].decode('latin1'); full=(dp(parent)+'/'+name).lstrip('/')
        ct=(p+0x1e+nl+1+3)&~3   # chunk table: after null-terminated name, 4-byte aligned
        files[full]={'entry':p,'dataoff':dataoff,'ct':ct,'size':size}
        offs.append(dataoff)
    return files, sorted(offs)


def _target_list(files):
    """Auto-discover every dialog/menu interface to scale+center: all of ui/interfaces/frontend/ and
    ui/interfaces/multiplayer/, minus the in-game HUD panels (which anchor to screen edges, not centre).
    Plus the backend ESC menu and the overlay target. Robust to new dialogs across game versions."""
    inc = ('ui/interfaces/frontend/', 'ui/interfaces/multiplayer/')
    exc = ('in_game', 'panel', 'background', 'lobby_chat', '/dir.lqd22')   # in-game HUD / not a centred dialog
    targets = [p for p in files if p.endswith('.gas')
               and any(p.startswith(x) for x in inc)
               and not any(x in p for x in exc)]
    targets.append('ui/interfaces/backend/in_game_menu/in_game_menu.gas')   # the ESC/pause menu
    # Map/journal cloth-map scaling is DISABLED for now: under wined3d the map RENDERS, but SCALING its
    # [t:object_view] leaves the cloth map "out of line" (the 3D content offsets from its enlarged frame —
    # the same actor-offset the character models show at the 1920 backbuffer). Native (unscaled) renders
    # aligned. Revisit once the object_view viewport offset is solved. See docs/MAP_PORTING_TODO.md.
    # targets += [p for p in files if p.endswith('.gas') and _is_map_target(p) and '/dir.lqd22' not in p]
    targets = sorted(set(targets))
    if OVERLAY_TARGET not in targets:
        targets.append(OVERLAY_TARGET)
    return targets


def _edit_one(d, files, offs, path, scale, version, write_end, log, canvas=None):
    """Transform one interface in `d` and write it back — in its slot if it fits, else relocated to the
    end of the tank (removes the per-slot budget limit). Raises on any problem (caller skips it)."""
    f = files[path]; ct = f['ct']; base = f['dataoff']+0x33c; size = f['size']
    nch = (size + BLK-1)//BLK
    RAW = 16
    u = b''
    for i in range(nch):
        _uc, cs, _pad, rel = struct.unpack('<4I', d[ct+8+16*i:ct+8+16*i+16])
        dec = zlib.decompressobj(); part = dec.decompress(d[base+rel:base+rel+cs+64])
        u += part + (d[base+rel+cs:base+rel+cs+RAW] if i < nch-1 else b'')
    assert len(u) == size, f"decompressed {len(u)} != size {size}"
    iw, ih = INTENDED.get(path, (800, 600))
    if path == OVERLAY_TARGET:
        u2 = insert_overlay(u, version)
    elif _is_map_target(path):
        # Cloth-map screens: scale+center the whole screen INCLUDING the [t:object_view] map viewport, so the
        # map grows with its frame. (Blanking on scale was a DXVK bug, fixed by forcing wined3d.)
        u2 = scale_center(u, scale, iw, ih, scale_object_view=True,
                          fill_object_view=path in MAP_FILL_TARGETS, canvas=canvas)
    else:
        if path == MP_PROVIDER:
            u = customize_mp_provider(u)
        # Scale the frontend 3D preview viewports (Continue party model, hero-select paperdoll, etc.) with
        # their panels. This was long kept verbatim for two reasons, both now resolved: (1) under DXVK the
        # scaled viewport rendered blank — fixed by forcing wined3d; (2) editing a frontend interface forces a
        # dir.lqd22 recompile that used to hide the save party list (bug-1) — neutralised by the exe
        # save-footprint bypass. Result: the preview model renders in its panel instead of at the native
        # 800x600 corner. See docs/MAP_PORTING_TODO.md and the object_view notes in scale_center().
        u2 = scale_center(u, scale, iw, ih, scale_object_view=True, canvas=canvas)
    if 'frontend_help' in path:   # tight slot: drop center_height (minor vertical-align) to fit
        u2 = re.sub(rb'[ \t]*center_height = true;\r?\n', b'', u2)
    blocks = [u2[i:i+BLK] for i in range(0, len(u2), BLK)] or [b'']
    new_nch = len(blocks)
    # the chunk table is fixed-size (nch entries) inside the FileEntry -> can't grow the chunk count.
    assert new_nch == nch, f"chunk count changed {nch}->{new_nch}"
    recs = []; off = 0
    for i, b in enumerate(blocks):
        if i == new_nch-1:
            c = zlib.compress(b, 9); tail = b''; ucf = len(b)
        else:
            c = zlib.compress(b[:BLK-RAW], 9); tail = b[BLK-RAW:]; ucf = BLK
        recs.append((ucf, c, tail, off)); off += len(c) + len(tail)
    total = off
    nxt = min([o for o in offs if o > f['dataoff']], default=f['dataoff']+total+0x10000)
    budget = nxt - f['dataoff']
    if total <= budget:
        base_w = base; where = "in-place"
    else:   # relocate the data to the end of the tank (unlimited space)
        base_w = (write_end[0] + 3) & ~3
        struct.pack_into('<I', d, f['entry']+8, base_w - 0x33c)   # FileEntry.dataoff (rel to 0x33c)
        write_end[0] = base_w + total
        where = f"relocated@0x{base_w:x}"
    if len(d) < base_w + total:
        d.extend(b'\x00' * (base_w + total - len(d)))
    for ucf, c, tail, rel in recs:
        d[base_w+rel:base_w+rel+len(c)] = c
        d[base_w+rel+len(c):base_w+rel+len(c)+len(tail)] = tail
    struct.pack_into('<I', d, f['entry']+4, len(u2))
    struct.pack_into('<I', d, f['entry']+0xc, zlib.crc32(u2)&0xffffffff)
    struct.pack_into('<I', d, ct+0, total); struct.pack_into('<I', d, ct+4, BLK)
    for i, (ucf, c, tail, rel) in enumerate(recs):
        struct.pack_into('<4I', d, ct+8+16*i, ucf, len(c), len(tail), rel)
    lqd = path.rsplit('/',1)[0] + '/dir.lqd22'
    if lqd in files:
        lqd_t = struct.unpack('<Q', d[files[lqd]['entry']+0x10:files[lqd]['entry']+0x18])[0]
        struct.pack_into('<Q', d, f['entry']+0x10, lqd_t + 10_000_000)
    log(f"OK {path}: {len(u)}->{len(u2)}B, {nch} chunk(s), {where}")


def edit_tank(tank, scale=1.5, backup=True, version=None, log=print, canvas=None):
    """Edit a DSg2Tank (.ds2res) in place: scale/center the menu interfaces + inject the overlay.
    Writes a `.pre-edit.bak` next to it (if backup). Requires the exe CRC check disabled.
    `canvas=(w,h)` = the game window's client size to centre into (default: the Linux 1912x1046)."""
    version = version or __version__
    cw, ch = canvas or (CW, CH)
    d = bytearray(open(tank,'rb').read())
    files, offs = parse(d)
    if backup:
        shutil.copy2(tank, tank+'.pre-edit.bak')
    write_end = [len(d)]   # append cursor for relocated files (list = mutable closure)
    ok = skipped = 0
    for path in _target_list(files):
        if path not in files:
            log(f"SKIP {path}: not in tank"); skipped += 1; continue
        try:
            _edit_one(d, files, offs, path, scale, version, write_end, log, canvas=(cw, ch))
            ok += 1
        except Exception as e:  # noqa: BLE001 — one bad interface must not abort the whole build
            log(f"SKIP {path}: {e}"); skipped += 1
    open(tank,'wb').write(d)
    _bak = f"; backup {tank}.pre-edit.bak" if backup else ""
    log(f"PATCHED {tank} (scale x{scale}, canvas {cw}x{ch}; {ok} ok, {skipped} skipped){_bak}")


if __name__ == '__main__':
    import sys
    _tank = sys.argv[1]
    _scale = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    edit_tank(_tank, _scale)
