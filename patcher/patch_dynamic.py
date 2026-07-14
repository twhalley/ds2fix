#!/usr/bin/env python3
# Thin CLI wrapper — the exe-patch logic now lives in ds2fix_core.exe_patch (importable, byte-identical).
# Usage: patch_dynamic.py <pristine-exe> <out-exe>   (env: MENU_169, CHOKE, WS169, RES_W, RES_H)
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ds2fix_core.exe_patch import patch_exe

if __name__ == '__main__':
    patch_exe(sys.argv[1], sys.argv[2],
              menu169=os.environ.get('MENU_169', '1') != '0',
              choke=os.environ.get('CHOKE', '1') != '0',
              ws169=os.environ.get('WS169', '1') != '0',
              res_w=int(os.environ.get('RES_W', '1920')),
              res_h=int(os.environ.get('RES_H', '1080')))
