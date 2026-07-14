#!/usr/bin/env python3
# General DSg2Tank in-place .gas editor: apply a text transform to one or more UI .gas files in a tank,
# recompress within each file's gap, fix size/CRC/chunk-table, and bump the .gas FILETIME past its
# compiled dir.lqd22 cache so the engine recompiles from the edited source. (Requires the CRC
# content-integrity check to be disabled in the exe — see patch_dynamic.py.)
import sys, struct, zlib, re, shutil

TANK  = sys.argv[1]
SCALE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
CW, CH = 1912, 1046
OVERRIDES = { (1, 576, 284, 599): (1590, 12, 1900, 40) }   # text_version "ds2fix 0.1" -> top-right corner

# UI interfaces to transform (each paired with its compiled cache to invalidate)
TARGETS = [
    'ui/interfaces/frontend/main_menu/main_menu.gas',
    'ui/interfaces/frontend/frontend_help/frontend_help.gas',
    'ui/interfaces/frontend/create_party/create_party.gas',  # multi-chunk (17504B / 2 chunks)
    'ui/interfaces/frontend/difficulty_menu/difficulty_menu.gas',  # Easy/Normal/Hard selector
    'ui/interfaces/frontend/load_game/load_game.gas',  # campaign "Select Difficulty" (Merc/Vet/Elite)
    'ui/interfaces/backend/in_game_menu/in_game_menu.gas',  # in-game ESC/pause menu (640x480 canvas)
]

# Per-interface authoring canvas override for centering. Everything (incl. in_game_menu, whose CONTENT
# is centered at ~400,300 despite its 640x480 intended_resolution) centers correctly against the default
# 800x600, so this stays empty; it's the hook for any future interface authored off an 800 canvas.
INTENDED = {}

# In-game "ds2fix 0.1" overlay: injected (NOT scaled) into the always-on data_bar HUD interface.
# Modeled on the proven game_timer text node. is_right_anchor + no bottom anchor => the element tracks
# the TOP-RIGHT of the live canvas at any resolution; right_anchor = 800 - rect.x1 (canvas is 800 wide,
# offset from right edge to the element's LEFT). justify=right hugs the text to the right edge.
OVERLAY_TARGET = 'ui/interfaces/backend/data_bar/data_bar.gas'
OVERLAY_ANCHOR = b'\t[t:button,n:button_collect_loot_bg]'   # first top-level child; insert node before it
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
                b'\t  b topmost = true;\r\n'   # data_bar's other top-level texts (text_pause/prompter) all
                b'\t  b visible = true;\r\n'   # set visible explicitly; without it the node defaults hidden
                b'\t}\r\n')
if OVERLAY_TARGET not in TARGETS:
    TARGETS.append(OVERLAY_TARGET)

BLK = 0x4000  # 16384-byte uncompressed block per zlib chunk

def scale_center(u, iw=800, ih=600):
    ox = (CW - iw*SCALE) / 2      # centre the intended canvas (iw x ih) inside the 1912x1046 output
    oy = (CH - ih*SCALE) / 2
    def repl(m):
        x1,y1,x2,y2 = (int(v) for v in m.group(1,2,3,4))
        if (x1,y1,x2,y2) in OVERRIDES:
            return b'rect = %d,%d,%d,%d' % OVERRIDES[(x1,y1,x2,y2)]
        return b'rect = %d,%d,%d,%d' % (round(ox+x1*SCALE), round(oy+y1*SCALE),
                                        round(ox+x2*SCALE), round(oy+y2*SCALE))
    # tolerate whitespace after commas: some rects are written "rect = 161, 365, 802, 640"
    # (e.g. load_game info_frame) — without \s* they'd be skipped and left at raw 800-canvas coords.
    u = re.subn(rb'rect = (-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)', repl, u)[0]
    u = re.sub(rb'[ \t]+(\r?\n)', rb'\1', u)          # trailing whitespace
    u = re.sub(rb'(?m)^[ \t]*\r?\n', b'', u)           # blank lines
    # scaling widens the numbers; for tight tank slots reclaim bytes by dedenting. Safe ONLY when the
    # file has no skrit `[[ ]]` code block (gas node/statement syntax is whitespace-insensitive).
    if b'[[' not in u:
        u = re.sub(rb'(?m)^[ \t]+', b'', u)
    return u

def insert_overlay(u):
    if b'text_ds2fix' in u:   # idempotent
        return u
    i = u.find(OVERLAY_ANCHOR)
    assert i != -1, "data_bar overlay anchor element not found"
    u = u[:i] + OVERLAY_NODE + u[i:]
    u = re.sub(rb'[ \t]+(\r?\n)', rb'\1', u)     # reclaim slot space: trailing whitespace
    u = re.sub(rb'(?m)^[ \t]*\r?\n', b'', u)      # + blank lines (safe for gas / skrit)
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

d = bytearray(open(TANK,'rb').read())
files, offs = parse(d)
shutil.copy2(TANK, TANK+'.pre-edit.bak')
for path in TARGETS:
    f = files[path]; ct = f['ct']; base = f['dataoff']+0x33c; size = f['size']
    nch = (size + BLK-1)//BLK
    RAW = 16   # each non-final 16384-block = zlib(first 16368 bytes) + this many raw content bytes
    # reconstruct full content: per chunk decompress the zlib part, then append its raw tail
    u = b''
    for i in range(nch):
        _uc, cs, _pad, rel = struct.unpack('<4I', d[ct+8+16*i:ct+8+16*i+16])
        dec = zlib.decompressobj(); part = dec.decompress(d[base+rel:base+rel+cs+64])
        u += part + (d[base+rel+cs:base+rel+cs+RAW] if i < nch-1 else b'')
    assert len(u) == size, f"{path}: decompressed {len(u)} != size {size}"
    iw, ih = INTENDED.get(path, (800, 600))
    u2 = insert_overlay(u) if path == OVERLAY_TARGET else scale_center(u, iw, ih)
    if 'frontend_help' in path:   # tight slot: drop center_height (minor vertical-align) to fit
        u2 = re.sub(rb'[ \t]*center_height = true;\r?\n', b'', u2)
    # re-split into 16384-byte blocks; non-final = zlib(first 16368) + 16 raw tail, final = zlib(all)
    blocks = [u2[i:i+BLK] for i in range(0, len(u2), BLK)] or [b'']
    new_nch = len(blocks)
    assert new_nch == nch, f"{path}: chunk count changed {nch}->{new_nch} (would shift entries)"
    recs = []   # (uc_field, comp_bytes, raw_tail, reloff)
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
    # write compressed chunk data + raw tails
    for ucf, c, tail, rel in recs:
        d[base+rel:base+rel+len(c)] = c
        d[base+rel+len(c):base+rel+len(c)+len(tail)] = tail
    # FileEntry: uncompressed size + CRC32(full)
    struct.pack_into('<I', d, f['entry']+4, len(u2))
    struct.pack_into('<I', d, f['entry']+0xc, zlib.crc32(u2)&0xffffffff)
    # chunk table: [total, blocksize] + per chunk [uc_field, comp, rawtail, reloff]
    struct.pack_into('<I', d, ct+0, total); struct.pack_into('<I', d, ct+4, BLK)
    for i, (ucf, c, tail, rel) in enumerate(recs):
        struct.pack_into('<4I', d, ct+8+16*i, ucf, len(c), len(tail), rel)
    # bump this .gas past its dir.lqd22 cache
    lqd = path.rsplit('/',1)[0] + '/dir.lqd22'
    if lqd in files:
        lqd_t = struct.unpack('<Q', d[files[lqd]['entry']+0x10:files[lqd]['entry']+0x18])[0]
        struct.pack_into('<Q', d, f['entry']+0x10, lqd_t + 10_000_000)
    print(f"OK {path}: {len(u)}->{len(u2)} uncomp, {nch} chunk(s), span {total}/{budget}, .lqd bumped")

open(TANK,'wb').write(d)
print(f"PATCHED {TANK} (scale x{SCALE}); backup {TANK}.pre-edit.bak")
