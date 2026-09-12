#!/usr/bin/env python3
"""ds2fix unit tests (stdlib unittest — no external deps).

Run: python -m unittest discover -s tests   (or  python tests/test_ds2fix.py)

Game-file-independent tests always run. The exe/tank tests run only if a pristine DungeonSiege2 install is
reachable (dev machine); otherwise they're skipped, so CI stays green without shipping copyrighted files.
"""
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ds2fix_core import __version__, tank, mods, exe_patch  # noqa: E402

# A pristine exe, if present on this machine (dev only): $DS2_PRISTINE_EXE, the Linux dev path, or the
# pinned/auto-detected install's `.ds2fix-pristine` backup (Windows dev box). Never shipped/committed.
def _find_pristine_exe():
    cands = [Path(os.environ["DS2_PRISTINE_EXE"])] if os.environ.get("DS2_PRISTINE_EXE") else []
    cands.append(Path("/run/media/legion/4tb_btrfs/Games/ds2-gog/prefix/drive_c/Games/Dungeon Siege II/"
                      "DungeonSiege2.exe.ds2fix-pristine"))
    try:
        import ds2fix
        gd = ds2fix.detect_gamedir(None, pin=False)
        cands.append(ds2fix.pristine_paths(gd)[0])
    except (SystemExit, Exception):  # noqa: BLE001 — no install here: fine, tests skip
        pass
    return next((c for c in cands if c.is_file()), cands[-1])


_PRISTINE_EXE = _find_pristine_exe()


class TestVersion(unittest.TestCase):
    def test_version_is_short_enough_for_menu_slot(self):
        # the in-game menu label lives in a fixed 17-char slot: "ds2fix <version>" must fit.
        self.assertLessEqual(len(f"ds2fix {__version__}"), 17, "version too long for the exe menu-label slot")

    def test_version_nonempty(self):
        self.assertRegex(__version__, r"^\d+\.\d+")


class TestTankTransform(unittest.TestCase):
    def test_scale_center_scales_and_centers(self):
        out = tank.scale_center(b"[t:window,n:w]\nrect = 100,110,600,540;\n", 1.5)
        self.assertIn(b"rect = 506,238,1256,883", out)

    def test_object_view_verbatim_by_default(self):
        # frontend preview viewport must stay untouched unless scale_object_view is set
        src = b"[t:object_view,n:hero]\nrect = 100,110,600,540;\n"
        self.assertIn(b"rect = 100,110,600,540", tank.scale_center(src, 1.5))
        self.assertIn(b"rect = 506,238,1256,883",
                      tank.scale_center(src, 1.5, scale_object_view=True))

    def test_fill_object_view_stretches(self):
        src = b"[t:object_view,n:map_view]\nrect = 0,0,800,600;\n"
        out = tank.scale_center(src, 1.5, scale_object_view=True, fill_object_view=True)
        self.assertIn(b"rect = 0,0,%d,%d" % (tank.CW, tank.CH), out)

    def test_canvas_param_centres_into_given_client_size(self):
        # Windows borderless 2560x1440: 800x600 @2.0 = 1600x1200 -> centred at (480,120)
        src = b"[t:window,n:w]\nrect = 0,0,800,600;\n"
        self.assertIn(b"rect = 480,120,2080,1320", tank.scale_center(src, 2.0, canvas=(2560, 1440)))
        # default canvas is unchanged (Linux 1912x1046 client of a captioned 1920x1080 window)
        self.assertEqual(tank.scale_center(src, 1.5), tank.scale_center(src, 1.5, canvas=(1912, 1046)))
        # the version-label override anchors to the canvas' right edge
        lab = b"[t:text,n:text_version]\nrect = 1,576,284,599;\n"
        self.assertIn(b"rect = 2238,12,2548,40", tank.scale_center(lab, 2.0, canvas=(2560, 1440)))
        self.assertIn(b"rect = 1590,12,1900,40", tank.scale_center(lab, 1.5))
        # fill stretches to the given canvas
        mv = b"[t:object_view,n:map_view]\nrect = 0,0,800,600;\n"
        self.assertIn(b"rect = 0,0,2560,1440",
                      tank.scale_center(mv, 2.0, scale_object_view=True, fill_object_view=True, canvas=(2560, 1440)))

    def test_is_map_target(self):
        self.assertTrue(tank._is_map_target("ui/interfaces/backend/journal/books/mapbook/mapbook.gas"))
        self.assertTrue(tank._is_map_target("ui/interfaces/backend/teleport/teleport.gas"))
        self.assertFalse(tank._is_map_target("ui/interfaces/frontend/main_menu/main_menu.gas"))

    def test_overlay_insert_idempotent(self):
        u = b"[data_bar]\n{\n\t[t:button,n:button_collect_loot_bg]\n\t{}\n}\n"
        once = tank.insert_overlay(u, "0.1.5")
        self.assertIn(b"text_ds2fix", once)
        self.assertIn(b"ds2fix 0.1.5", once)
        self.assertEqual(once, tank.insert_overlay(once, "0.1.5"))   # second call is a no-op


class TestPortraitRect(unittest.TestCase):
    def test_formula_matches_ingame_generator(self):
        r = exe_patch.portrait_grab_rect
        # exact in-game values: float32 0.00125/0.0016667 are slightly UNDER, and _ftol truncates ->
        # 1920 wide gives 911 (not 912), 1440 high gives 664 (not 665). Mirroring the game is the point.
        self.assertEqual(r(1920, 1080), (911, 498, 975, 562))
        self.assertEqual(r(2560, 1440), (1215, 664, 1279, 728))
        self.assertEqual(r(1280, 1024), (619, 488, 683, 552))   # GPG fudge table (+12,+16)
        self.assertEqual(r(1024, 768), (494, 360, 558, 424))    # (+8,+6)
        self.assertEqual(r(640, 480), (301, 218, 365, 282))      # (-2,-3)
        self.assertEqual(r(800, 600)[2:], (r(800, 600)[0] + 64, r(800, 600)[1] + 64))   # always 64x64


class TestLauncherDefaults(unittest.TestCase):
    def test_auto_scale_and_canvas(self):
        import ds2fix
        self.assertEqual(ds2fix.auto_scale(1080), 1.5)
        self.assertEqual(ds2fix.auto_scale(1440), 2.0)
        self.assertEqual(ds2fix.auto_scale(720), 1.0)
        self.assertEqual(ds2fix.canvas_for(1920, 1080, borderless=False), (1912, 1046))   # Linux legacy
        self.assertEqual(ds2fix.canvas_for(2560, 1440, borderless=True), (2560, 1440))    # Windows

    def test_play_command_windows_is_borderless_windowed(self):
        import ds2fix
        if not ds2fix.IS_WINDOWS:
            self.skipTest("Windows launch path")
        cmd, env, note = ds2fix.play_command(Path("C:/game"), 2560, 1440, 2560, 1440, False)
        self.assertIn("fullscreen=false", cmd)
        self.assertIn("width=2560", cmd)
        self.assertIn("highdpiaware", env.get("__COMPAT_LAYER", "").lower())
        self.assertIn("borderless", note)


class TestMods(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name) / "game"
        (self.game / "Resources").mkdir(parents=True)
        self.dl = Path(self.tmp.name) / "dl"
        self.dl.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _fake_ds2res(self, name="hd.ds2res", body=b"DSg2Tank fake payload"):
        p = self.dl / name
        p.write_bytes(body)
        return p

    def test_install_and_remove_ds2res(self):
        src = self._fake_ds2res()
        logs = []
        mods.install(self.game, "hd-textures", src=str(src), log=logs.append)
        self.assertTrue((self.game / "Resources" / "hd.ds2res").exists())
        self.assertIn("hd-textures", mods.installed_mods(self.game))
        mods.remove(self.game, "hd-textures", log=logs.append)
        self.assertFalse((self.game / "Resources" / "hd.ds2res").exists())
        self.assertNotIn("hd-textures", mods.installed_mods(self.game))

    def test_install_from_zip(self):
        z = self.dl / "hd_pack.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("readme.txt", "hi")
            zf.writestr("HD_Textures.ds2res", b"DSg2Tank")
        mods.install(self.game, "hd-textures", src=str(z), log=lambda m: None)
        self.assertTrue((self.game / "Resources" / "HD_Textures.ds2res").exists())

    def test_unknown_mod_errors(self):
        with self.assertRaises(SystemExit):
            mods.install(self.game, "does-not-exist", src=str(self._fake_ds2res()), log=lambda m: None)

    def test_sha512_mismatch_blocks_without_force(self):
        mods.REGISTRY["hd-textures"]["sha512"] = {"0" * 128}
        try:
            with self.assertRaises(SystemExit):
                mods.install(self.game, "hd-textures", src=str(self._fake_ds2res()), log=lambda m: None)
            # --force overrides
            mods.install(self.game, "hd-textures", src=str(self._fake_ds2res()), force=True, log=lambda m: None)
            self.assertIn("hd-textures", mods.installed_mods(self.game))
        finally:
            mods.REGISTRY["hd-textures"]["sha512"] = set()

    def test_sha512_matches_installs(self):
        src = self._fake_ds2res(body=b"known-good bytes")
        good = mods.sha512(src)
        mods.REGISTRY["storage-vault"]["sha512"] = {good}
        try:
            mods.install(self.game, "storage-vault", src=str(src), log=lambda m: None)
            self.assertIn("storage-vault", mods.installed_mods(self.game))
        finally:
            mods.REGISTRY["storage-vault"]["sha512"] = set()


@unittest.skipUnless(_PRISTINE_EXE.exists(), "pristine DungeonSiege2.exe not available")
class TestExePatch(unittest.TestCase):
    def test_patch_sets_laa_and_is_detectable_and_idempotent(self):
        patched = exe_patch.patch_exe(str(_PRISTINE_EXE), None, log=lambda m: None)
        pe = struct.unpack("<I", patched[0x3c:0x40])[0]
        chars = struct.unpack("<H", patched[pe + 22:pe + 24])[0]
        self.assertTrue(chars & 0x0020, "LAA bit not set")
        self.assertIn(b".ds2fix\x00", bytes(patched))   # ds2fix section marker => detectable as patched
        # re-patching the pristine input must be byte-stable
        again = exe_patch.patch_exe(str(_PRISTINE_EXE), None, log=lambda m: None)
        self.assertEqual(bytes(patched), bytes(again))

    def test_window_style_captioned_vs_borderless(self):
        fo = 0x5ebc45 - 0x400000
        pristine = _PRISTINE_EXE.read_bytes()
        self.assertEqual(pristine[fo:fo + 4], (0x10ce0000).to_bytes(4, "little"), "pristine style imm changed")
        captioned = exe_patch.patch_exe(str(_PRISTINE_EXE), None, log=lambda m: None)
        self.assertEqual(captioned[fo:fo + 4], (0x10ca0000).to_bytes(4, "little"), "THICKFRAME not dropped")
        borderless = exe_patch.patch_exe(str(_PRISTINE_EXE), None, borderless=True, log=lambda m: None)
        self.assertEqual(borderless[fo:fo + 4], (0x90000000).to_bytes(4, "little"), "WS_POPUP not applied")
        # the two variants differ ONLY in that one style immediate
        diff = [i for i, (a, b) in enumerate(zip(captioned, borderless)) if a != b]
        self.assertTrue(diff and all(fo <= i < fo + 4 for i in diff), f"unexpected extra diffs: {diff[:8]}")

    def test_leader_portrait_grab_rect_follows_frontend_res(self):
        sites = (0x4435ac, 0x4435b3, 0x4435ba, 0x4435c1)
        rd = lambda b, va: int.from_bytes(b[va - 0x400000:va - 0x400000 + 4], 'little')
        pristine = _PRISTINE_EXE.read_bytes()
        self.assertEqual([rd(pristine, s) for s in sites], [380, 277, 444, 341])
        p = exe_patch.patch_exe(str(_PRISTINE_EXE), None, res_w=2560, res_h=1440, log=lambda m: None)
        self.assertEqual([rd(p, s) for s in sites], [380, 277, 444, 341])   # off by default (experimental)
        p = exe_patch.patch_exe(str(_PRISTINE_EXE), None, res_w=2560, res_h=1440, portrait_rect=True,
                                log=lambda m: None)
        self.assertEqual([rd(p, s) for s in sites], list(exe_patch.portrait_grab_rect(2560, 1440)))
        p = exe_patch.patch_exe(str(_PRISTINE_EXE), None, res_w=1920, res_h=1080, canvas_w=1912, canvas_h=1046,
                                portrait_rect=True, log=lambda m: None)
        self.assertEqual([rd(p, s) for s in sites], list(exe_patch.portrait_grab_rect(1912, 1046)))
        p = exe_patch.patch_exe(str(_PRISTINE_EXE), None, menu169=False, portrait_rect=True, log=lambda m: None)
        self.assertEqual([rd(p, s) for s in sites], [380, 277, 444, 341])   # 800x600 frontend: stock rect

    def test_save_footprint_check_bypassed(self):
        # IsContentCrcAcceptable (FUN_004139d0) must be forced to "mov al,1 ; ret 4" so saves always
        # list/load regardless of the install's content signature.
        pristine = bytearray(_PRISTINE_EXE.read_bytes())
        fo = 0x4139d0 - 0x400000
        self.assertEqual(bytes(pristine[fo:fo + 5]), bytes([0x55, 0x8b, 0xec, 0x8b, 0x0d]),
                         "pristine save-footprint site changed — RE stale")
        patched = exe_patch.patch_exe(str(_PRISTINE_EXE), None, log=lambda m: None)
        self.assertEqual(bytes(patched[fo:fo + 5]), bytes([0xb0, 0x01, 0xc2, 0x04, 0x00]),
                         "save-footprint check not bypassed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
