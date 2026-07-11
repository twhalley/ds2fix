import zlib, os, sys

path = sys.argv[1]
outdir = sys.argv[2]
os.makedirs(outdir, exist_ok=True)
data = open(path, 'rb').read()
n = len(data)

# zlib stream headers (CMF=0x78 common, plus FLG variants)
magics = [b'\x78\x01', b'\x78\x5e', b'\x78\x9c', b'\x78\xda']
positions = []
for m in magics:
    s = 0
    while True:
        i = data.find(m, s)
        if i == -1:
            break
        positions.append(i)
        s = i + 1
positions = sorted(set(positions))

recovered = []
seen = set()
for pos in positions:
    try:
        d = zlib.decompressobj()
        out = d.decompress(data[pos:], 8_000_000)
        if len(out) < 120:
            continue
        printable = sum(1 for b in out if b in (9,10,13) or 32 <= b <= 126)
        if printable / len(out) < 0.88:
            continue
        h = hash(out[:400])
        if h in seen:
            continue
        seen.add(h)
        recovered.append((pos, out))
    except Exception:
        continue

# write anything that looks like a gas UI/interface definition
hits = 0
allblob = []
for pos, out in recovered:
    txt = out.decode('latin-1', 'replace')
    allblob.append(txt)
    low = txt.lower()
    if any(k in low for k in ('screen_rect', 'screen_edge_tracking', '[interface', 'common_screen', 'placement', 'anchor', 'world_frame', 'paperdoll')):
        hits += 1
        with open(os.path.join(outdir, f'ui_{pos:08x}.gas'), 'w') as f:
            f.write(txt)

with open(os.path.join(outdir, '_ALL.txt'), 'w') as f:
    f.write('\n\n==== STREAM ====\n\n'.join(allblob))

print(f"file={path} size={n}")
print(f"zlib candidate offsets: {len(positions)}")
print(f"recovered text streams: {len(recovered)}")
print(f"UI-looking streams written: {hits}")
