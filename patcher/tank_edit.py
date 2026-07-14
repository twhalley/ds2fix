#!/usr/bin/env python3
# Thin CLI wrapper — the tank-edit logic now lives in ds2fix_core.tank (importable, byte-identical).
# Usage: tank_edit.py <tank.ds2res> [scale]
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ds2fix_core.tank import edit_tank

if __name__ == '__main__':
    edit_tank(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 1.5)
