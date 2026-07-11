#!/usr/bin/env python3
# In-place patch of main_menu.gas inside Logic.ds2res: shift all rect x-coords by +SHIFT to
# center the 800-wide frontend menu in the ~1912 canvas. Recompresses (fits the original slot),
# updates the FileEntry uncompressed size + CRC32 + chunk-table sizes. Backs up the tank first.
import sys, re, zlib, struct, shutil, os

TANK   = sys.argv[1]
SHIFT  = int(sys.argv[2]) if len(sys.argv) > 2 else 556
ENTRY  = 0x79db90          # main_menu.gas FileEntry
DATAOFF = 0x210810         # its zlib data (data_off + header DataOffset)
ORIG_COMP_SLOT = 0x82d     # 2093 bytes available
CT = ENTRY + 0x1e + 14     # chunk table (after 'main_menu.gas\0')

d = bytearray(open(TANK, 'rb').read())

# recover pristine uncompressed content from the tank itself
uncompressed = zlib.decompress(bytes(d[DATAOFF:DATAOFF+ORIG_COMP_SLOT+16]))
assert b'[t:interface,n:main_menu]' in uncompressed
old_size = struct.unpack('<I', d[ENTRY+4:ENTRY+8])[0]
assert old_size == len(uncompressed), (old_size, len(uncompressed))

# shift rect x-coords (binary regex; preserves \r\n and all formatting)
def repl(m):
    x1, y1, x2, y2 = (int(v) for v in m.group(1, 2, 3, 4))
    return b'rect = %d,%d,%d,%d' % (x1+SHIFT, y1, x2+SHIFT, y2)
edited, n = re.subn(rb'rect = (-?\d+),(-?\d+),(-?\d+),(-?\d+)', repl, uncompressed)
print(f"shifted {n} rects by +{SHIFT}; size {len(uncompressed)} -> {len(edited)}")

# reclaim the few bytes the shift added WITHOUT touching code: strip trailing whitespace on each
# line, then remove blank/whitespace-only lines. Both are invisible to the .gas parser (unlike
# stripping // comments, which merged a line and CRASHED the game).
before = len(edited)
edited = re.sub(rb'[ \t]+(\r?\n)', rb'\1', edited)          # trailing whitespace
edited = re.sub(rb'(?m)^[ \t]*\r?\n', b'', edited)           # blank lines
print(f"whitespace-trimmed; size {before} -> {len(edited)}")

# the real budget is the gap to the next file's data, not just the original slot
BUDGET = 2100
comp = zlib.compress(edited, 9)
print(f"recompressed (lvl9): {len(comp)} bytes (budget = {BUDGET})")
assert len(comp) <= BUDGET, "recompressed too big even after stripping comments!"

new_crc = zlib.crc32(edited) & 0xffffffff

# --- apply patch ---
shutil.copy2(TANK, TANK + '.pre-menucenter.bak')
# 1) write new compressed data (leftover tail bytes in slot are unused / between-file gap)
d[DATAOFF:DATAOFF+len(comp)] = comp
# 2) FileEntry: uncompressed size + CRC
struct.pack_into('<I', d, ENTRY+4, len(edited))
struct.pack_into('<I', d, ENTRY+0xc, new_crc)
# 3) chunk table: compressed size @+0, uncompressed @+8, compressed @+0xc
struct.pack_into('<I', d, CT+0x0, len(comp))
struct.pack_into('<I', d, CT+0x8, len(edited))
struct.pack_into('<I', d, CT+0xc, len(comp))

open(TANK, 'wb').write(d)
print(f"PATCHED {TANK}")
print(f"  uncompressed {old_size} -> {len(edited)}, comp {len(comp)}, crc 0x{new_crc:08x}")
print(f"  backup: {TANK}.pre-menucenter.bak")
