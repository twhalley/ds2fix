#!/usr/bin/env python3
# DSg2Tank .gas editor (importable core). Same logic as tank_edit.py, parameterised. Output is
# byte-identical to the script for the same scale. Scales+centers the frontend & in-game ESC menus into
# the 16:9 canvas and injects the top-right "ds2fix 0.1" overlay into the always-on data_bar HUD.
# Requires the exe CRC content-check disabled (see exe_patch.py).
import struct, zlib, re, shutil

CW, CH = 1912, 1046
OVERRIDES = { (1, 576, 284, 599): (1590, 12, 1900, 40) }   # text_version "ds2fix 0.1" -> top-right corner

TARGETS = [
    'ui/interfaces/frontend/main_menu/main_menu.gas',
    'ui/interfaces/frontend/frontend_help/frontend_help.gas',
    'ui/interfaces/frontend/create_party/create_party.gas',        # multi-chunk (17504B / 2 chunks)
    'ui/interfaces/frontend/difficulty_menu/difficulty_menu.gas',   # Easy/Normal/Hard selector
    'ui/interfaces/frontend/load_game/load_game.gas',               # campaign "Select Difficulty"
    'ui/interfaces/backend/in_game_menu/in_game_menu.gas',          # in-game ESC/pause menu (640x480)
]

# Per-interface authoring-canvas override for centering (default 800x600 works for everything so far).
INTENDED = {}

# In-game "ds2fix 0.1" overlay: injected into the always-on data_bar HUD (needs `visible = true`).
OVERLAY_TARGET = 'ui/interfaces/backend/data_bar/data_bar.gas'
OVERLAY_ANCHOR = b'\t[t:button,n:button_collect_loot_bg]'
OVERLAY_NODE = (b'\t[t:text,n:text_ds2fix]\r\n'
                b'\t{\r\n'
                b'\t  x font_color = -1;\r\n'
                b'\t  i draw_order = 200;\r\n'
                b'\t\tfont_type = b_gui_fnt_16p_ringbearer-gold;\r\n'
                b'\t  b is_right_anchor = true;\r\n'
                b'\t\tjustify = right;\r\n'
                b'\t\trect = 655,4,797,28;\r\n'
                b'\t  i right_anchor = 145;\r\n'
                b'\t\ttext = "ds2fix 0.1";\r\n'
                b'\t  b topmost = true;\r\n'
                b'\t  b visible = true;\r\n'
                b'\t}\r\n')
if OVERLAY_TARGET not in TARGETS:
    TARGETS.append(OVERLAY_TARGET)

BLK = 0x4000  # 16384-byte uncompressed block per zlib chunk


def scale_center(u, scale, iw=800, ih=600):
    ox = (CW - iw*scale) / 2      # centre the intended canvas (iw x ih) inside the 1912x1046 output
    oy = (CH - ih*scale) / 2
    def repl(m):
        x1,y1,x2,y2 = (int(v) for v in m.group(1,2,3,4))
        if (x1,y1,x2,y2) in OVERRIDES:
            return b'rect = %d,%d,%d,%d' % OVERRIDES[(x1,y1,x2,y2)]
        return b'rect = %d,%d,%d,%d' % (round(ox+x1*scale), round(oy+y1*scale),
                                        round(ox+x2*scale), round(oy+y2*scale))
    u = re.subn(rb'rect = (-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)', repl, u)[0]
    u = re.sub(rb'[ \t]+(\r?\n)', rb'\1', u)          # trailing whitespace
    u = re.sub(rb'(?m)^[ \t]*\r?\n', b'', u)           # blank lines
    if b'[[' not in u:                                 # dedent skrit-free files to reclaim slot bytes
        u = re.sub(rb'(?m)^[ \t]+', b'', u)
    return u


def insert_overlay(u):
    if b'text_ds2fix' in u:   # idempotent
        return u
    i = u.find(OVERLAY_ANCHOR)
    assert i != -1, "data_bar overlay anchor element not found"
    u = u[:i] + OVERLAY_NODE + u[i:]
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


def edit_tank(tank, scale=1.5, backup=True, log=print):
    """Edit a DSg2Tank (.ds2res) in place: scale/center the menu interfaces + inject the overlay.
    Writes a `.pre-edit.bak` next to it (if backup). Requires the exe CRC check disabled."""
    d = bytearray(open(tank,'rb').read())
    files, offs = parse(d)
    if backup:
        shutil.copy2(tank, tank+'.pre-edit.bak')
    for path in TARGETS:
        f = files[path]; ct = f['ct']; base = f['dataoff']+0x33c; size = f['size']
        nch = (size + BLK-1)//BLK
        RAW = 16
        u = b''
        for i in range(nch):
            _uc, cs, _pad, rel = struct.unpack('<4I', d[ct+8+16*i:ct+8+16*i+16])
            dec = zlib.decompressobj(); part = dec.decompress(d[base+rel:base+rel+cs+64])
            u += part + (d[base+rel+cs:base+rel+cs+RAW] if i < nch-1 else b'')
        assert len(u) == size, f"{path}: decompressed {len(u)} != size {size}"
        iw, ih = INTENDED.get(path, (800, 600))
        u2 = insert_overlay(u) if path == OVERLAY_TARGET else scale_center(u, scale, iw, ih)
        if 'frontend_help' in path:   # tight slot: drop center_height (minor vertical-align) to fit
            u2 = re.sub(rb'[ \t]*center_height = true;\r?\n', b'', u2)
        blocks = [u2[i:i+BLK] for i in range(0, len(u2), BLK)] or [b'']
        new_nch = len(blocks)
        assert new_nch == nch, f"{path}: chunk count changed {nch}->{new_nch} (would shift entries)"
        recs = []
        off = 0
        for i, b in enumerate(blocks):
            last = i == new_nch-1
            if last:
                c = zlib.compress(b, 9); tail = b''; ucf = len(b)
            else:
                c = zlib.compress(b[:BLK-RAW], 9); tail = b[BLK-RAW:]; ucf = BLK
            recs.append((ucf, c, tail, off)); off += len(c) + len(tail)
        total = off
        nxt = min([o for o in offs if o > f['dataoff']], default=f['dataoff']+total+0x10000)
        budget = nxt - f['dataoff']
        assert total <= budget, f"{path}: span {total} > budget {budget}"
        for ucf, c, tail, rel in recs:
            d[base+rel:base+rel+len(c)] = c
            d[base+rel+len(c):base+rel+len(c)+len(tail)] = tail
        struct.pack_into('<I', d, f['entry']+4, len(u2))
        struct.pack_into('<I', d, f['entry']+0xc, zlib.crc32(u2)&0xffffffff)
        struct.pack_into('<I', d, ct+0, total); struct.pack_into('<I', d, ct+4, BLK)
        for i, (ucf, c, tail, rel) in enumerate(recs):
            struct.pack_into('<4I', d, ct+8+16*i, ucf, len(c), len(tail), rel)
        lqd = path.rsplit('/',1)[0] + '/dir.lqd22'
        if lqd in files:
            lqd_t = struct.unpack('<Q', d[files[lqd]['entry']+0x10:files[lqd]['entry']+0x18])[0]
            struct.pack_into('<Q', d, f['entry']+0x10, lqd_t + 10_000_000)
        log(f"OK {path}: {len(u)}->{len(u2)} uncomp, {nch} chunk(s), span {total}/{budget}, .lqd bumped")
    open(tank,'wb').write(d)
    _bak = f"; backup {tank}.pre-edit.bak" if backup else ""
    log(f"PATCHED {tank} (scale x{scale}){_bak}")


if __name__ == '__main__':
    import sys
    _tank = sys.argv[1]
    _scale = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
    edit_tank(_tank, _scale)
