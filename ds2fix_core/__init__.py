"""ds2fix_core — the reusable DS2 patcher engine (exe binary patch + DSg2Tank .gas edit).

Shared by the CLI (ds2fix.py) and GUI. Output is byte-identical to the original patcher/ scripts.
"""
from .exe_patch import patch_exe
from .tank import edit_tank

__all__ = ["patch_exe", "edit_tank"]
__version__ = "0.1"
