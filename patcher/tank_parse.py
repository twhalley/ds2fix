#!/usr/bin/env python3
# Minimal DSg2Tank (Dungeon Siege 2 .ds2res) directory parser.
# Reconstructs full logical paths for every file, so we can find the main_menu interface's
# path (for a loose-file override) and its compressed data location.
import sys, struct, zlib

path = sys.argv[1]
want = sys.argv[2] if len(sys.argv) > 2 else None   # optional substring filter on path
d = open(path, 'rb').read()

assert d[:8] == b'DSg2Tank', "not a DSg2Tank"
dirset_off  = struct.unpack('<I', d[0x0c:0x10])[0]
fileset_off = struct.unpack('<I', d[0x10:0x14])[0]

# ---- DirSet: count, then count*uint32 entry offsets (relative to dirset_off) ----
dcount = struct.unpack('<I', d[dirset_off:dirset_off+4])[0]
dir_entry_offs = struct.unpack('<%dI' % dcount, d[dirset_off+4:dirset_off+4+4*dcount])
dirs = {}   # entryOff -> (parentOff, name)
for eo in dir_entry_offs:
    p = dirset_off + eo
    parent = struct.unpack('<I', d[p:p+4])[0]
    # layout: parent(4) childCount(4) time(8) nameLen(2) name
    namelen = struct.unpack('<H', d[p+16:p+18])[0]
    name = d[p+18:p+18+namelen].split(b'\x00')[0].decode('latin1')
    dirs[eo] = (parent, name)

def dir_path(eo):
    parts = []
    seen = set()
    while eo != 0 and eo in dirs and eo not in seen:
        seen.add(eo)
        parent, name = dirs[eo]
        if name:
            parts.append(name)
        eo = parent
    return '/'.join(reversed(parts))

# ---- FileSet: count, then count*uint32 entry offsets (relative to fileset_off) ----
fcount = struct.unpack('<I', d[fileset_off:fileset_off+4])[0]
file_entry_offs = struct.unpack('<%dI' % fcount, d[fileset_off+4:fileset_off+4+4*fcount])
files = []
for eo in file_entry_offs:
    p = fileset_off + eo
    parent, size, data_off, crc = struct.unpack('<IIII', d[p:p+16])
    fmt = struct.unpack('<H', d[p+24:p+26])[0]
    namelen = struct.unpack('<H', d[p+28:p+30])[0]
    name = d[p+30:p+30+namelen].split(b'\x00')[0].decode('latin1')
    full = (dir_path(parent) + '/' + name).lstrip('/')
    files.append({'path': full, 'size': size, 'data_off': data_off,
                  'fmt': fmt, 'entry': p, 'namelen': namelen, 'crc': crc})

print(f"dirs={dcount} files={fcount}")
gas = [f for f in files if f['path'].lower().endswith('.gas')]
print(f".gas files: {len(gas)}")
# show frontend/ui-ish paths
for f in files:
    lp = f['path'].lower()
    if want:
        if want.lower() in lp:
            print(f"  fmt={f['fmt']} size={f['size']:>7} off=0x{f['data_off']:x} entry=0x{f['entry']:x}  {f['path']}")
    else:
        if any(k in lp for k in ('frontend', 'main_menu', 'menu', 'interface')) and lp.endswith('.gas'):
            print(f"  fmt={f['fmt']} size={f['size']:>7} off=0x{f['data_off']:x}  {f['path']}")
