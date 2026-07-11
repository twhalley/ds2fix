#!/usr/bin/env python3
# DS2 dynamic UI-canvas patch: make UIShell::SetScreenSize (FUN_0073be90) compute the
# canvas from the LIVE main-window rect (*(0xbcb28c))[0xb4-0xac, 0xb8-0xb0] instead of the
# passed (possibly stale) args, with a null-pointer fallback to the passed args.
# Adds a tiny executable section to hold the stub; routes through the existing 12-byte
# int3 cave after the function.
import sys, struct, os

orig, dst = sys.argv[1], sys.argv[2]
MENU_169 = os.environ.get('MENU_169', '1') != '0'   # native 16:9 menu (default ON; MENU_169=0 disables)
d = bytearray(open(orig, 'rb').read())

# ---- PATCH 0: version label. The menu bottom-left shows a runtime-built string from the format
# "$MSG$Version - %S". Overwrite it with "$MSG$ds2fix 0.1" so the menu reads "ds2fix 0.1" (also a
# proof-of-injection indicator). Same-or-shorter length, padded with nulls. Idempotent.
_vold = b'$MSG$Version - %S\x00'
_vnew = b'$MSG$ds2fix 0.1\x00'
_vi = d.find(_vold)
if _vi > 0:
    d[_vi:_vi+len(_vnew)] = _vnew
    for _k in range(len(_vnew), len(_vold)):
        d[_vi+_k] = 0
    print('OK: version label -> "ds2fix 0.1"')
elif d.find(_vnew) < 0:
    print('WARN: version string not found (already patched or exe differs)')

# ---- parse PE headers ----
pe = struct.unpack('<I', d[0x3c:0x40])[0]
nsec = struct.unpack('<H', d[pe+6:pe+8])[0]
optsz = struct.unpack('<H', d[pe+20:pe+22])[0]
opt = pe + 24
imgbase = struct.unpack('<I', d[opt+28:opt+32])[0]
secalign = struct.unpack('<I', d[opt+32:opt+36])[0]
filealign = struct.unpack('<I', d[opt+36:opt+40])[0]
sizeofimg_off = opt + 56
sectab = opt + optsz

def align(v, a): return (v + a - 1) // a * a

# last section by virtual addr
maxend_va = 0
for i in range(nsec):
    o = sectab + i*40
    vsz, va, rsz, rptr = struct.unpack('<IIII', d[o+8:o+24])
    maxend_va = max(maxend_va, va + vsz)

new_va = align(maxend_va, secalign)               # RVA of new section
new_raw = align(len(d), filealign)                # file offset (append at EOF, aligned)
new_rawsize = filealign                            # one file-alignment block
new_vsize = 0x80
S = imgbase + new_va                               # absolute VA of stub start

def va2fo(va): return va - imgbase + 0x1000 - 0x1000  # .text: fo == va-imgbase (raw==rva here)
# .text has PointerToRawData==VirtualAddress==0x1000, so file offset = VA - imgbase.
def txt_fo(va): return va - imgbase

# ---- build the stub (placed at absolute VA S) ----
def rel32(frm_end, to): return struct.pack('<i', to - frm_end)
stub = bytearray()
stub += bytes([0x8b,0x7d,0x08])                       # mov edi,[ebp+8]      (restore passed w)
stub += bytes([0xa1,0x8c,0xb2,0xbc,0x00])             # mov eax, ds:0xbcb28c (window ptr)
stub += bytes([0x85,0xc0])                            # test eax,eax
stub += bytes([0x74,0x18])                            # jz .done (skip 24 bytes)
stub += bytes([0x8b,0xb8,0xb4,0x00,0x00,0x00])        # mov edi,[eax+0xb4]  (right)
stub += bytes([0x2b,0xb8,0xac,0x00,0x00,0x00])        # sub edi,[eax+0xac]  (-left = width)
stub += bytes([0x8b,0x98,0xb8,0x00,0x00,0x00])        # mov ebx,[eax+0xb8]  (bottom)
stub += bytes([0x2b,0x98,0xb0,0x00,0x00,0x00])        # sub ebx,[eax+0xb0]  (-top = height)
# .done: jmp back to 0x73be9e (the original stores)
back = 0x73be9e
jmp_at = S + len(stub)
stub += bytes([0xe9]) + rel32(jmp_at+5, back)
assert len(stub) == 0x29, hex(len(stub))

# ---- verify + apply the two in-place patches (from pristine bytes) ----
assert bytes(d[txt_fo(0x73be9b):txt_fo(0x73be9b)+3]) == bytes([0x8b,0x7d,0x08]), "patch-site mismatch"
assert all(b==0xCC for b in d[txt_fo(0x73bee4):txt_fo(0x73bee4)+12]), "cave not int3"
# patch site: short jmp to the int3 cave + nop  (unchanged from the hardcoded version)
d[txt_fo(0x73be9b):txt_fo(0x73be9b)+3] = bytes([0xEB,0x47,0x90])
# cave: jmp rel32 to the stub in the new section
cave = 0x73bee4
d[txt_fo(cave):txt_fo(cave)+5] = bytes([0xE9]) + rel32(cave+5, S)
# fill rest of the 12-byte cave with int3
for k in range(5,12): d[txt_fo(cave)+k] = 0xCC

# ---- append the new section ----
# header
o = sectab + nsec*40
name = b'.ds2fix\x00'
d[o:o+8] = name
struct.pack_into('<IIII', d, o+8, new_vsize, new_va, new_rawsize, new_raw)
struct.pack_into('<III', d, o+24, 0, 0, 0)             # relocs/linenums
struct.pack_into('<I', d, o+36, 0x60000020)            # CODE | EXECUTE | READ
# bump section count + SizeOfImage
struct.pack_into('<H', d, pe+6, nsec+1)
struct.pack_into('<I', d, sizeofimg_off, align(new_va+new_vsize, secalign))
# grow file to new_raw and write section data (stub + int3 pad)
if len(d) < new_raw: d += b'\x00' * (new_raw - len(d))
body = bytearray(stub) + b'\xCC' * (new_rawsize - len(stub))
d[new_raw:new_raw+new_rawsize] = body

if MENU_169:
    # ---- PATCH 2+3 (experimental, gated by MENU_169=1): force the frontend/menu window to 1920x1080.
    # Rewrite the {800,600} immediates the frontend uses: 2 sites in the WorldState transition
    # FUN_00423071, and the window-creation defaults in FUN_005f109a. NOTE: incomplete — DS2 threads
    # the frontend 800x600 through more paths (config read, FUN_0049a129), so this alone does NOT yet
    # make the menu 16:9. Kept behind the flag for further work.
    NEW_W = (1920).to_bytes(4, 'little'); NEW_H = (1080).to_bytes(4, 'little')
    OLD_W = (800).to_bytes(4, 'little');  OLD_H = (600).to_bytes(4, 'little')
    for w_imm_va, h_imm_va in [(0x4231d8, 0x4231df), (0x424dd7, 0x424dde),
                               (0x5f12c2, 0x5f1316), (0x5f1372, 0x5f1382)]:
        assert bytes(d[txt_fo(w_imm_va):txt_fo(w_imm_va)+4]) == OLD_W, f"menu-w mismatch @{w_imm_va:#x}"
        assert bytes(d[txt_fo(h_imm_va):txt_fo(h_imm_va)+4]) == OLD_H, f"menu-h mismatch @{h_imm_va:#x}"
        d[txt_fo(w_imm_va):txt_fo(w_imm_va)+4] = NEW_W
        d[txt_fo(h_imm_va):txt_fo(h_imm_va)+4] = NEW_H

    # ---- PATCH 4: window-sizer CHOKE POINT — FUN_005ebeba stores w=[ebp-8], h=[ebp-4] from its
    # {w,h} arg (bytes 89 4d f8 89 45 fc @0x5ebed1), then sentinel-checks/MoveWindows. Trampoline
    # those two stores into a 2nd .ds2fix stub that forces 1920x1080 -> EVERY sizer caller (frontend
    # 800x600, gameplay sentinel->config) becomes 1920x1080 in one shot. Back-target 0x5ebed7.
    S2 = S + 0x30
    stub2 = bytearray()
    stub2 += bytes([0xc7,0x45,0xf8]) + NEW_W          # mov [ebp-0x8], 1920
    stub2 += bytes([0xc7,0x45,0xfc]) + NEW_H          # mov [ebp-0x4], 1080
    back2 = 0x5ebed7
    stub2 += bytes([0xe9]) + struct.pack('<i', back2 - (S2 + len(stub2) + 5))  # jmp back
    d[new_raw+0x30 : new_raw+0x30+len(stub2)] = stub2  # place at section+0x30 (over int3 pad)
    # trampoline at 0x5ebed1 (6 bytes: the two stores): jmp rel32 -> S2 ; nop
    assert bytes(d[txt_fo(0x5ebed1):txt_fo(0x5ebed1)+6]) == bytes([0x89,0x4d,0xf8,0x89,0x45,0xfc]), "sizer store mismatch"
    d[txt_fo(0x5ebed1):txt_fo(0x5ebed1)+6] = bytes([0xe9]) + struct.pack('<i', S2 - (0x5ebed1+5)) + bytes([0x90])
    print(f"OK: [MENU_169] sizer choke-point forced 1920x1080 (FUN_005ebeba -> stub @{S2:#x})")

    # ---- PATCH 5: THE real one — window CREATION (FUN_005f109a) reads config "width"/"height"
    # (keys @0xa95a20/0xa95a18) which SUCCEED with 800/600, so the 1920/1080 fallback (patch above)
    # was never used. NOP the two `jne` that skip the fallback so the fallback ALWAYS wins ->
    # window is created at 1920x1080. This is what makes the frontend/menu actually 16:9.
    for jne_va in (0x5f12f0, 0x5f1344):   # width jne, height jne (both `75 0c`)
        assert bytes(d[txt_fo(jne_va):txt_fo(jne_va)+2]) == bytes([0x75,0x0c]), f"jne mismatch @{jne_va:#x}"
        d[txt_fo(jne_va):txt_fo(jne_va)+2] = bytes([0x90,0x90])   # nop; nop -> always take fallback
    print("OK: [MENU_169] NOP'd config-read jne @0x5f12f0/0x5f1344 -> creation forced to fallback 1920x1080")

    # ---- PATCH 6: THE DIRECT ONE — the actual CreateWindowExA (ret=0x5f2273, found via Wine relay)
    # computes nWidth/nHeight inline right before the call. Overwrite those computations with
    # immediate 1920/1080 so the window is created 16:9 regardless of every upstream 800x600 source.
    #   height @0x5f2220: `8b 40 0c 2b 41 04` (mov eax,[eax+0xc]; sub eax,[ecx+4]) -> mov eax,1080 ; nop
    #   width  @0x5f2233: `8b 40 08 2b 01`    (mov eax,[eax+8];   sub eax,[ecx])   -> mov eax,1920
    assert bytes(d[txt_fo(0x5f2220):txt_fo(0x5f2220)+6]) == bytes([0x8b,0x40,0x0c,0x2b,0x41,0x04]), "cwx-h mismatch"
    assert bytes(d[txt_fo(0x5f2233):txt_fo(0x5f2233)+5]) == bytes([0x8b,0x40,0x08,0x2b,0x01]), "cwx-w mismatch"
    d[txt_fo(0x5f2220):txt_fo(0x5f2220)+6] = bytes([0xb8]) + NEW_H + bytes([0x90])   # mov eax,1080 ; nop
    d[txt_fo(0x5f2233):txt_fo(0x5f2233)+5] = bytes([0xb8]) + NEW_W                   # mov eax,1920
    print("OK: [MENU_169] CreateWindowExA args forced to 1920x1080 (@0x5f2220/0x5f2233)")
    print("OK: [MENU_169] frontend/creation res 800x600 -> 1920x1080 at 4 sites")

open(dst, 'wb').write(d)
print(f"OK: new section .ds2fix RVA={new_va:#x} VA={S:#x} raw={new_raw:#x}")
print(f"    stub {len(stub)} bytes; cave jmp -> {S:#x}; SizeOfImage={align(new_va+new_vsize,secalign):#x}")
