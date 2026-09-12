#!/usr/bin/env python3
# DS2 exe patcher (importable core). Same logic as the original patch_dynamic.py, parameterised so the
# CLI/GUI can call it directly. Output is byte-identical to the script for the same options.
#
# Makes UIShell::SetScreenSize (FUN_0073be90) compute the canvas from the LIVE main-window rect
# (*(0xbcb28c))[0xb4-0xac, 0xb8-0xb0] instead of the passed (stale) args, plus CRC-disable, difficulty
# auto-unlock, non-resizable window, the version label, and (MENU_169) the native-16:9 menu patches.
import struct
try:
    from ._version import __version__            # imported as a package (normal)
except ImportError:
    from _version import __version__             # run directly as a script


def portrait_grab_rect(w, h):
    """The 64x64 backbuffer rect DS2's IN-GAME portrait generator (FUN_004f25bb) grabs for a w x h window:
    x = trunc(w*0.00125*380), y = trunc(h*0.0016667*277) (the float32 constants @0xaa5e44/0xaa5e3c, _ftol
    truncation), plus GPG's per-resolution fudge. At 800x600 this is the frontend's hard-coded 380,277."""
    x = int(w * 0.0012499999720603228 * 380.0); y = int(h * 0.0016666667070239782 * 277.0)
    fx, fy = {(1280, 1024): (12, 16), (1024, 768): (8, 6), (640, 480): (-2, -3)}.get((int(w), int(h)), (0, 0))
    x += fx; y += fy
    return (x, y, x + 64, y + 64)


def patch_exe(orig, dst=None, menu169=True, choke=True, ws169=True, res_w=1920, res_h=1080,
              version=None, log=print, borderless=False, dyncanvas=True, canvas_w=None, canvas_h=None,
              portrait_rect=False):
    """Patch a pristine DungeonSiege2.exe. `orig`/`dst` are file paths (dst optional -> returns bytes).
    Returns the patched bytes. Raises AssertionError if a patch site doesn't match (wrong/patched exe).
    `borderless`: give the game window a WS_POPUP (no caption/frame) style instead of the non-resizable
    captioned one — the Windows launcher's borderless-fullscreen mode (see PATCH WIN).
    `canvas_w/h`: the game window's CLIENT size (default: res) — what the leader-portrait grab rect is
    computed from (see PATCH PORTRAIT). `portrait_rect=True` enables that EXPERIMENTAL patch (off by
    default: verified NOT sufficient on native Windows). `dyncanvas=False` is a debug/bisect switch only."""
    MENU_169, CHOKE, WS169 = menu169, choke, ws169
    version = version or __version__
    with open(orig, 'rb') as _f:
        d = bytearray(_f.read())

    # ---- PATCH 0: menu version label "$MSG$Version - %S" -> "$MSG$ds2fix <version>" (idempotent).
    # Fixed slot: len("$MSG$ds2fix <version>") must be <= len("$MSG$Version - %S"); if the version is
    # too long for the menu, drop it to just "ds2fix" (the gameplay overlay still shows it in full).
    _vold = b'$MSG$Version - %S\x00'
    _label = f'ds2fix {version}'
    _vnew = b'$MSG$' + _label.encode('latin1') + b'\x00'
    if len(_vnew) > len(_vold):
        _label, _vnew = 'ds2fix', b'$MSG$ds2fix\x00'
    _vi = d.find(_vold)
    if _vi > 0:
        d[_vi:_vi+len(_vnew)] = _vnew
        for _k in range(len(_vnew), len(_vold)):
            d[_vi+_k] = 0
        log(f'OK: version label -> "{_label}"')
    elif d.find(b'$MSG$ds2fix') < 0:
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

    # ---- PATCH SAVEFOOTPRINT: make saves ALWAYS list + load, regardless of content signature.
    # DS2 stamps every save's summary with a "content_crc" = a hash of the installed resource set at
    # save time. When enumerating the Single Player / Continue list AND when loading, it calls
    # IsContentCrcAcceptable (FUN_004139d0), which returns FALSE if the save's crc != the current
    # install's crc (and isn't in a tiny built-in whitelist) -> the save is SILENTLY HIDDEN from the
    # list and refused on load. Re-patching the UI tank or installing a data mod changes the resource
    # set -> changes the crc -> previously-fine saves vanish (the "disappear/reappear" bug). Force the
    # check to always accept (mov al,1 ; ret 4). Its ONLY two callers are the two save-summary readers
    # (list @0x41ee87, load @0x41c5a0); MP content-matching uses a separate path, so multiplayer is
    # unaffected. Reversible; idempotent.
    _sf_fo = 0x4139d0 - 0x400000
    if bytes(d[_sf_fo:_sf_fo+5]) == bytes([0x55,0x8b,0xec,0x8b,0x0d]):
        d[_sf_fo:_sf_fo+5] = bytes([0xb0,0x01,0xc2,0x04,0x00])   # mov al,1 ; ret 4
        log('OK: save content-footprint check bypassed (FUN_004139d0 -> accept; saves always list/load)')
    elif bytes(d[_sf_fo:_sf_fo+5]) == bytes([0xb0,0x01,0xc2,0x04,0x00]):
        log('OK: save content-footprint check already bypassed')
    else:
        log(f'WARN: save-footprint site unexpected ({bytes(d[_sf_fo:_sf_fo+5]).hex()}); skipped')

    # ---- PATCH UNLOCK: auto-unlock all campaign difficulties (FUN_004171d7 -> always completed).
    _unlock_fo = 0x417226 - 0x400000
    if bytes(d[_unlock_fo:_unlock_fo+2]) == bytes([0x8a,0xd8]):
        d[_unlock_fo:_unlock_fo+2] = bytes([0xb3,0x01])   # mov bl,1 (force "completed")
        log('OK: campaign difficulties auto-unlocked (FUN_004171d7 -> always completed; SP+MP)')
    elif bytes(d[_unlock_fo:_unlock_fo+2]) == bytes([0xb3,0x01]):
        log('OK: campaign difficulties already unlocked')
    else:
        log(f'WARN: unlock site unexpected ({bytes(d[_unlock_fo:_unlock_fo+2]).hex()}); skipped')

    # ---- PATCH WIN: window style. The top-level window style is the immediate in
    # `mov [ebp-4], 0x10ce0000` @0x5ebc42 (imm @0x5ebc45; WS_VISIBLE|WS_CAPTION|WS_SYSMENU|WS_THICKFRAME|
    # WS_MINIMIZEBOX) inside the style-builder the windowed CreateWindowExA (@0x5f226d) uses. Two modes:
    #  * default: non-resizable — drop WS_THICKFRAME (0xce -> 0xca @0x5ebc47). DS2 never rebuilds the
    #    swapchain on WM_SIZE, so dragging the border used to black it out. (Linux/Wine + gamescope path.)
    #  * borderless: WS_POPUP|WS_VISIBLE (0x90000000). No caption/frame, so AdjustWindowRect adds nothing
    #    and the client area == the render res exactly; launched `fullscreen=false` at the monitor's res
    #    this IS borderless fullscreen — clean alt-tab, no exclusive mode switch (which on native Windows
    #    also ran the whole frontend at 800x600, ignoring the MENU_169 patches). Windows launcher default.
    _win_imm = 0x5ebc45 - 0x400000
    _cur = bytes(d[_win_imm:_win_imm+4])
    if borderless:
        if _cur in (b'\x00\x00\xce\x10', b'\x00\x00\xca\x10'):
            d[_win_imm:_win_imm+4] = (0x90000000).to_bytes(4, 'little')
            log('OK: window made BORDERLESS (WS_POPUP|WS_VISIBLE @0x5ebc45) -> borderless fullscreen when res == monitor')
        elif _cur == b'\x00\x00\x00\x90':
            log('OK: window already borderless')
        else:
            log(f'WARN: window-style site unexpected ({_cur.hex()}); skipped')
    else:
        _win_fo = 0x5ebc47 - 0x400000
        if d[_win_fo] == 0xce:
            d[_win_fo] = 0xca
            log('OK: window made non-resizable (WS_THICKFRAME removed) -> no resize black-screen')
        elif d[_win_fo] == 0xca:
            log('OK: window already non-resizable')

    # ---- PATCH MPBTN: enable the Multiplayer button. UIFrontend::TransitionToMain (@0x44c2d3)
    # UNCONDITIONALLY calls UIButton::DisableButton on "button_multiplayer" every time the main menu
    # shows (GPG hard-disabled MP in the retail build). NOP that 5-byte call (@0x44c37a) so the button
    # stays enabled -> LAN + Internet(direct-IP) multiplayer become reachable. No GameSpy needed for those.
    _mp_fo = 0x44c37a - 0x400000
    if bytes(d[_mp_fo:_mp_fo+5]) == bytes([0xe8,0x31,0xa0,0x31,0x00]):
        d[_mp_fo:_mp_fo+5] = b'\x90\x90\x90\x90\x90'
        log('OK: Multiplayer button enabled (DisableButton NOP @0x44c37a)')
    elif bytes(d[_mp_fo:_mp_fo+5]) == bytes([0x90,0x90,0x90,0x90,0x90]):
        log('OK: Multiplayer button already enabled')
    else:
        log(f'WARN: MP-button site unexpected ({bytes(d[_mp_fo:_mp_fo+5]).hex()}); skipped')

    # ---- parse PE headers ----
    pe = struct.unpack('<I', d[0x3c:0x40])[0]

    # ---- PATCH LAA: Large-Address-Aware. DS2 is a 32-bit exe capped at 2GB of address space; set
    # IMAGE_FILE_LARGE_ADDRESS_AWARE (0x0020) in the COFF Characteristics so it can use up to 4GB. This
    # matters once HD texture mods are installed (x4 upscales ~= 16x memory) — without it they OOM-crash
    # on larger areas. Pure PE-header bit; reversible; works under Wine on a 64-bit host too.
    _chr_fo = pe + 22
    _chars = struct.unpack('<H', d[_chr_fo:_chr_fo+2])[0]
    if not (_chars & 0x0020):
        struct.pack_into('<H', d, _chr_fo, _chars | 0x0020)
        log('OK: Large-Address-Aware enabled (2GB -> 4GB; for HD texture mods)')
    else:
        log('OK: already Large-Address-Aware')

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
    if dyncanvas:
        d[txt_fo(0x73be9b):txt_fo(0x73be9b)+3] = bytes([0xEB,0x47,0x90])
        cave = 0x73bee4
        d[txt_fo(cave):txt_fo(cave)+5] = bytes([0xE9]) + rel32(cave+5, S)
        for k in range(5,12): d[txt_fo(cave)+k] = 0xCC
    else:   # debug/bisect: keep SetScreenSize stock (section still appended for the MENU_169 stub)
        log('WARN: [debug] dynamic UI canvas stub DISABLED (SetScreenSize left stock)')

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
            log(f"OK: [MENU_169] sizer choke-point forced {_rw}x{_rh} (FUN_005ebeba -> stub @{S2:#x})")

        for jne_va in (0x5f12f0, 0x5f1344):
            assert bytes(d[txt_fo(jne_va):txt_fo(jne_va)+2]) == bytes([0x75,0x0c]), f"jne mismatch @{jne_va:#x}"
            d[txt_fo(jne_va):txt_fo(jne_va)+2] = bytes([0x90,0x90])
        log(f"OK: [MENU_169] NOP'd config-read jne @0x5f12f0/0x5f1344 -> creation forced to fallback {_rw}x{_rh}")

        assert bytes(d[txt_fo(0x5f2220):txt_fo(0x5f2220)+6]) == bytes([0x8b,0x40,0x0c,0x2b,0x41,0x04]), "cwx-h mismatch"
        assert bytes(d[txt_fo(0x5f2233):txt_fo(0x5f2233)+5]) == bytes([0x8b,0x40,0x08,0x2b,0x01]), "cwx-w mismatch"
        d[txt_fo(0x5f2220):txt_fo(0x5f2220)+6] = bytes([0xb8]) + NEW_H + bytes([0x90])   # mov eax,1080 ; nop
        d[txt_fo(0x5f2233):txt_fo(0x5f2233)+5] = bytes([0xb8]) + NEW_W                   # mov eax,1920
        log(f"OK: [MENU_169] CreateWindowExA args forced to {_rw}x{_rh} (@0x5f2220/0x5f2233)")
        log(f"OK: [MENU_169] frontend/creation res 800x600 -> {_rw}x{_rh} at 4 sites")

        # ---- PATCH PORTRAIT: the party LEADER's HUD portrait (stock DS2 bug above 1280 px wide; black on
        # native D3D9, face-low + green clear under Wine; members 2..8 fine). A DS2 portrait is a 64x64
        # backbuffer pixel-grab taken once after an ortho render of the head (fixed pixel size, viewport-
        # centred). The HERO's is taken by the FRONTEND generator (RCGeneratePortrait -> FUN_00443480) from a
        # rect HARD-CODED for the 800x600 frontend: {380,277}-{444,341} (imm32 @0x4435ac/b3/ba/c1). Companions
        # are generated IN-GAME (FUN_004f25bb) from a rect that follows the live window (portrait_grab_rect).
        # With MENU_169 the frontend runs at the game res, so the fixed rect grabs empty backbuffer. Mirror
        # the in-game formula for the window size the frontend will actually have (client size = canvas).
        # STATUS 2026-09-12: applied + verified on Windows 11 / D3D9 at 1920x1080 with a NEWLY created
        # party -> leader portrait STILL black (companions fine). Necessary-looking but not sufficient;
        # kept as an opt-in experiment (DS2FIX_PORTRAIT_RECT=1). See docs/TODO.md item 4 for the RE map.
        _cw = int(canvas_w or _rw); _ch = int(canvas_h or _rh)
        _rect = portrait_grab_rect(_cw, _ch)
        _sites = ((0x4435ac, 380), (0x4435b3, 277), (0x4435ba, 444), (0x4435c1, 341))
        _pre = [bytes(d[txt_fo(a):txt_fo(a)+4]) for a, _ in _sites]
        if not portrait_rect:
            pass   # experimental; off by default
        elif all(p == v.to_bytes(4, 'little') for p, (_, v) in zip(_pre, _sites)):
            for (a, _), v in zip(_sites, _rect):
                d[txt_fo(a):txt_fo(a)+4] = v.to_bytes(4, 'little')
            log(f"OK: [MENU_169][experimental] leader-portrait grab rect 380,277,444,341 -> {','.join(map(str, _rect))} "
                f"(frontend window {_cw}x{_ch}; FUN_00443480 @0x4435ac)")
        else:
            log(f"WARN: leader-portrait grab-rect site unexpected ({b''.join(_pre).hex()}); skipped")

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
              res_h=int(os.environ.get('RES_H', '1080')),
              borderless=os.environ.get('BORDERLESS', '0') == '1')
