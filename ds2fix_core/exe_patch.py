#!/usr/bin/env python3
# DS2 exe patcher (importable core). Same logic as the original patch_dynamic.py, parameterised so the
# CLI/GUI can call it directly. Output is byte-identical to the script for the same options.
#
# Makes UIShell::SetScreenSize (FUN_0073be90) compute the canvas from the LIVE main-window rect
# (*(0xbcb28c))[0xb4-0xac, 0xb8-0xb0] instead of the passed (stale) args, plus CRC-disable, difficulty
# auto-unlock, non-resizable window, the version label, and (MENU_169) the native-16:9 menu patches.
import struct


def patch_exe(orig, dst=None, menu169=True, choke=True, ws169=True, res_w=1920, res_h=1080, log=print):
    """Patch a pristine DungeonSiege2.exe. `orig`/`dst` are file paths (dst optional -> returns bytes).
    Returns the patched bytes. Raises AssertionError if a patch site doesn't match (wrong/patched exe)."""
    MENU_169, CHOKE, WS169 = menu169, choke, ws169
    d = bytearray(open(orig, 'rb').read())

    # ---- PATCH 0: version label "$MSG$Version - %S" -> "$MSG$ds2fix 0.1" (idempotent).
    _vold = b'$MSG$Version - %S\x00'
    _vnew = b'$MSG$ds2fix 0.1\x00'
    _vi = d.find(_vold)
    if _vi > 0:
        d[_vi:_vi+len(_vnew)] = _vnew
        for _k in range(len(_vnew), len(_vold)):
            d[_vi+_k] = 0
        log('OK: version label -> "ds2fix 0.1"')
    elif d.find(_vnew) < 0:
        log('WARN: version string not found (already patched or exe differs)')

    # ---- PATCH CRC: disable the tank content-integrity check (enables all .gas data mods).
    _v1 = 0x699df1 - 0x400000
    if bytes(d[_v1:_v1+6]) == bytes([0x55,0x8b,0xec,0x83,0xec,0x10]):
        d[_v1:_v1+6] = bytes([0xb8,0x01,0x00,0x00,0x00,0xc3])   # mov eax,1 ; ret
        log('OK: content-integrity CRC verify disabled (FUN_00699df1 -> return 1)')
    elif bytes(d[_v1:_v1+2]) == bytes([0xb8,0x01]):
        log('OK: CRC verify already disabled')
    else:
        log(f'WARN: CRC-verify site unexpected ({bytes(d[_v1:_v1+2]).hex()}); skipped')
    _crc_fo = 0x6457d6 - 0x400000
    if d[_crc_fo] == 0x74 and d[_crc_fo+1] == 0x45:
        d[_crc_fo] = 0xeb
        log('OK: secondary CRC verify disabled (je -> jmp @0x6457d6)')

    # ---- PATCH UNLOCK: auto-unlock all campaign difficulties (FUN_004171d7 -> always completed).
    _unlock_fo = 0x417226 - 0x400000
    if bytes(d[_unlock_fo:_unlock_fo+2]) == bytes([0x8a,0xd8]):
        d[_unlock_fo:_unlock_fo+2] = bytes([0xb3,0x01])   # mov bl,1 (force "completed")
        log('OK: campaign difficulties auto-unlocked (FUN_004171d7 -> always completed; SP+MP)')
    elif bytes(d[_unlock_fo:_unlock_fo+2]) == bytes([0xb3,0x01]):
        log('OK: campaign difficulties already unlocked')
    else:
        log(f'WARN: unlock site unexpected ({bytes(d[_unlock_fo:_unlock_fo+2]).hex()}); skipped')

    # ---- PATCH WIN: non-resizable window (drop WS_THICKFRAME: 0xce imm @0x5ebc47 -> 0xca).
    _win_fo = 0x5ebc47 - 0x400000
    if d[_win_fo] == 0xce:
        d[_win_fo] = 0xca
        log('OK: window made non-resizable (WS_THICKFRAME removed) -> no resize black-screen')
    elif d[_win_fo] == 0xca:
        log('OK: window already non-resizable')

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

    maxend_va = 0
    for i in range(nsec):
        o = sectab + i*40
        vsz, va, rsz, rptr = struct.unpack('<IIII', d[o+8:o+24])
        maxend_va = max(maxend_va, va + vsz)

    new_va = align(maxend_va, secalign)
    new_raw = align(len(d), filealign)
    new_rawsize = filealign
    new_vsize = 0x80
    S = imgbase + new_va

    def txt_fo(va): return va - imgbase   # .text: PointerToRawData==VirtualAddress==0x1000

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
    back = 0x73be9e
    jmp_at = S + len(stub)
    stub += bytes([0xe9]) + rel32(jmp_at+5, back)
    assert len(stub) == 0x29, hex(len(stub))

    # ---- verify + apply the two in-place patches (from pristine bytes) ----
    assert bytes(d[txt_fo(0x73be9b):txt_fo(0x73be9b)+3]) == bytes([0x8b,0x7d,0x08]), "patch-site mismatch"
    assert all(b==0xCC for b in d[txt_fo(0x73bee4):txt_fo(0x73bee4)+12]), "cave not int3"
    d[txt_fo(0x73be9b):txt_fo(0x73be9b)+3] = bytes([0xEB,0x47,0x90])
    cave = 0x73bee4
    d[txt_fo(cave):txt_fo(cave)+5] = bytes([0xE9]) + rel32(cave+5, S)
    for k in range(5,12): d[txt_fo(cave)+k] = 0xCC

    # ---- append the new section ----
    o = sectab + nsec*40
    d[o:o+8] = b'.ds2fix\x00'
    struct.pack_into('<IIII', d, o+8, new_vsize, new_va, new_rawsize, new_raw)
    struct.pack_into('<III', d, o+24, 0, 0, 0)
    struct.pack_into('<I', d, o+36, 0x60000020)            # CODE | EXECUTE | READ
    struct.pack_into('<H', d, pe+6, nsec+1)
    struct.pack_into('<I', d, sizeofimg_off, align(new_va+new_vsize, secalign))
    if len(d) < new_raw: d += b'\x00' * (new_raw - len(d))
    body = bytearray(stub) + b'\xCC' * (new_rawsize - len(stub))
    d[new_raw:new_raw+new_rawsize] = body

    if MENU_169:
        _rw = int(res_w); _rh = int(res_h)
        NEW_W = _rw.to_bytes(4, 'little'); NEW_H = _rh.to_bytes(4, 'little')
        OLD_W = (800).to_bytes(4, 'little');  OLD_H = (600).to_bytes(4, 'little')
        log(f"OK: [MENU_169] forced frontend resolution = {_rw}x{_rh}")
        _ws_sites = [(0x4231d8, 0x4231df), (0x424dd7, 0x424dde)] if WS169 else []
        for w_imm_va, h_imm_va in _ws_sites + [(0x5f12c2, 0x5f1316), (0x5f1372, 0x5f1382)]:
            assert bytes(d[txt_fo(w_imm_va):txt_fo(w_imm_va)+4]) == OLD_W, f"menu-w mismatch @{w_imm_va:#x}"
            assert bytes(d[txt_fo(h_imm_va):txt_fo(h_imm_va)+4]) == OLD_H, f"menu-h mismatch @{h_imm_va:#x}"
            d[txt_fo(w_imm_va):txt_fo(w_imm_va)+4] = NEW_W
            d[txt_fo(h_imm_va):txt_fo(h_imm_va)+4] = NEW_H
        if not WS169:
            log("OK: [WS169=0] WorldState logical size kept native 800x600 (@0x4231d8/0x424dd7)")

        if CHOKE:
            S2 = S + 0x30
            stub2 = bytearray()
            stub2 += bytes([0xc7,0x45,0xf8]) + NEW_W          # mov [ebp-0x8], 1920
            stub2 += bytes([0xc7,0x45,0xfc]) + NEW_H          # mov [ebp-0x4], 1080
            back2 = 0x5ebed7
            stub2 += bytes([0xe9]) + struct.pack('<i', back2 - (S2 + len(stub2) + 5))
            d[new_raw+0x30 : new_raw+0x30+len(stub2)] = stub2
            assert bytes(d[txt_fo(0x5ebed1):txt_fo(0x5ebed1)+6]) == bytes([0x89,0x4d,0xf8,0x89,0x45,0xfc]), "sizer store mismatch"
            d[txt_fo(0x5ebed1):txt_fo(0x5ebed1)+6] = bytes([0xe9]) + struct.pack('<i', S2 - (0x5ebed1+5)) + bytes([0x90])
            log(f"OK: [MENU_169] sizer choke-point forced 1920x1080 (FUN_005ebeba -> stub @{S2:#x})")

        for jne_va in (0x5f12f0, 0x5f1344):
            assert bytes(d[txt_fo(jne_va):txt_fo(jne_va)+2]) == bytes([0x75,0x0c]), f"jne mismatch @{jne_va:#x}"
            d[txt_fo(jne_va):txt_fo(jne_va)+2] = bytes([0x90,0x90])
        log("OK: [MENU_169] NOP'd config-read jne @0x5f12f0/0x5f1344 -> creation forced to fallback 1920x1080")

        assert bytes(d[txt_fo(0x5f2220):txt_fo(0x5f2220)+6]) == bytes([0x8b,0x40,0x0c,0x2b,0x41,0x04]), "cwx-h mismatch"
        assert bytes(d[txt_fo(0x5f2233):txt_fo(0x5f2233)+5]) == bytes([0x8b,0x40,0x08,0x2b,0x01]), "cwx-w mismatch"
        d[txt_fo(0x5f2220):txt_fo(0x5f2220)+6] = bytes([0xb8]) + NEW_H + bytes([0x90])   # mov eax,1080 ; nop
        d[txt_fo(0x5f2233):txt_fo(0x5f2233)+5] = bytes([0xb8]) + NEW_W                   # mov eax,1920
        log("OK: [MENU_169] CreateWindowExA args forced to 1920x1080 (@0x5f2220/0x5f2233)")
        log("OK: [MENU_169] frontend/creation res 800x600 -> 1920x1080 at 4 sites")

    if dst is not None:
        open(dst, 'wb').write(d)
        log(f"OK: new section .ds2fix RVA={new_va:#x} VA={S:#x} raw={new_raw:#x}")
        log(f"    stub {len(stub)} bytes; cave jmp -> {S:#x}; SizeOfImage={align(new_va+new_vsize,secalign):#x}")
    return bytes(d)


if __name__ == '__main__':
    import sys, os
    _orig, _dst = sys.argv[1], sys.argv[2]
    patch_exe(_orig, _dst,
              menu169=os.environ.get('MENU_169', '1') != '0',
              choke=os.environ.get('CHOKE', '1') != '0',
              ws169=os.environ.get('WS169', '1') != '0',
              res_w=int(os.environ.get('RES_W', '1920')),
              res_h=int(os.environ.get('RES_H', '1080')))
