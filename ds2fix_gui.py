#!/usr/bin/env python3
"""ds2fix GUI — a small cross-platform front-end for the Dungeon Siege II patcher.

Detect the install, configure resolution / UI scale / 16:9 menu, then Patch, Play, or Restore.
Uses Tkinter (Python stdlib) so it bundles into a single binary with no extra dependencies.
"""
import os, sys, threading, queue
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ds2fix as core   # detect_gamedir, do_patch, do_restore, do_play, _exe_is_patched, pristine_paths, IS_WINDOWS
from ds2fix_core import __version__


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        root.title(f"ds2fix {__version__} — Dungeon Siege II patcher")
        root.minsize(560, 460)
        pad = dict(padx=8, pady=4)

        # --- install row ---
        top = ttk.LabelFrame(root, text="Install")
        top.pack(fill="x", **pad)
        self.gamedir = tk.StringVar()
        row = ttk.Frame(top); row.pack(fill="x", padx=6, pady=6)
        ttk.Entry(row, textvariable=self.gamedir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=4)
        ttk.Button(row, text="Detect", command=self.detect).pack(side="left")
        self.state = ttk.Label(top, text="")
        self.state.pack(anchor="w", padx=6, pady=(0, 6))

        # --- options ---
        opt = ttk.LabelFrame(root, text="Options")
        opt.pack(fill="x", **pad)
        grid = ttk.Frame(opt); grid.pack(fill="x", padx=6, pady=6)
        self.res = tk.StringVar(value="1920x1080")
        self.scale = tk.StringVar(value="1.5")
        self.out = tk.StringVar(value="2560x1440")
        self.menu169 = tk.BooleanVar(value=True)
        self.fsr = tk.BooleanVar(value=True)
        ttk.Label(grid, text="Render resolution").grid(row=0, column=0, sticky="w")
        ttk.Combobox(grid, textvariable=self.res, width=12,
                     values=["1920x1080", "2560x1440", "3840x2160", "1440x1080", "1280x960"]
                     ).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(grid, text="UI scale").grid(row=1, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.scale, width=8).grid(row=1, column=1, sticky="w", padx=6)
        if not core.IS_WINDOWS:
            ttk.Label(grid, text="Output (monitor)").grid(row=2, column=0, sticky="w")
            ttk.Combobox(grid, textvariable=self.out, width=12,
                         values=["2560x1440", "1920x1080", "3840x2160"]
                         ).grid(row=2, column=1, sticky="w", padx=6)
            ttk.Checkbutton(grid, text="FSR upscaling", variable=self.fsr).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Checkbutton(grid, text="Native 16:9 menu (uncheck to restore 3D model previews)",
                        variable=self.menu169).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- actions ---
        act = ttk.Frame(root); act.pack(fill="x", **pad)
        self.btn_patch = ttk.Button(act, text="Patch", command=lambda: self.run(self._patch))
        self.btn_play = ttk.Button(act, text="Patch + Play", command=lambda: self.run(self._play))
        self.btn_restore = ttk.Button(act, text="Restore", command=lambda: self.run(self._restore))
        self.btn_patch.pack(side="left", padx=4)
        self.btn_play.pack(side="left", padx=4)
        self.btn_restore.pack(side="left", padx=4)

        # --- log ---
        self.log = scrolledtext.ScrolledText(root, height=12, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, **pad)

        self.detect(initial=True)
        self.root.after(100, self._drain)

    # ---- helpers ----
    def _log(self, msg):
        self.q.put(msg)

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _res(self, var):
        w, h = var.get().lower().split("x")
        return int(w), int(h)

    def _gd(self):
        return core.detect_gamedir(self.gamedir.get() or None)

    def _set_buttons(self, enabled):
        for b in (self.btn_patch, self.btn_play, self.btn_restore):
            b.configure(state="normal" if enabled else "disabled")

    def refresh_state(self):
        try:
            gd = self._gd()
            patched = core._exe_is_patched(gd / core.EXE_NAME)
            pexe, _ = core.pristine_paths(gd)
            self.state.configure(
                text=f"exe: {'PATCHED (ds2fix)' if patched else 'pristine'}   |   "
                     f"pristine backup: {'yes' if pexe.exists() else 'no'}")
        except SystemExit as e:
            self.state.configure(text=str(e))

    # ---- actions (run in worker thread) ----
    def browse(self):
        d = filedialog.askdirectory(title="Select the Dungeon Siege II folder (contains DungeonSiege2.exe)")
        if d:
            self.gamedir.set(d)
            self.refresh_state()

    def detect(self, initial=False):
        try:
            self.gamedir.set(str(core.detect_gamedir(self.gamedir.get() or None)))
            self.refresh_state()
        except SystemExit as e:
            if not initial:
                self._log(str(e))
            self.state.configure(text="install not found — use Browse…")

    def run(self, fn):
        self._set_buttons(False)
        def worker():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - surface any error to the log
                self._log(f"ERROR: {e}")
            finally:
                self.root.after(0, lambda: (self._set_buttons(True), self.refresh_state()))
        threading.Thread(target=worker, daemon=True).start()

    def _patch(self):
        gd = self._gd(); rw, rh = self._res(self.res)
        core.do_patch(gd, rw, rh, float(self.scale.get()), self.menu169.get(), log=self._log)

    def _restore(self):
        core.do_restore(self._gd(), log=self._log)

    def _play(self):
        gd = self._gd(); rw, rh = self._res(self.res); ow, oh = self._res(self.out)
        core.do_patch(gd, rw, rh, float(self.scale.get()), self.menu169.get(), log=self._log)
        core.do_play(gd, rw, rh, ow, oh, self.fsr.get(), spawn=True, log=self._log)
        self._log("launched — the game window should appear shortly.")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
