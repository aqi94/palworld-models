"""Print a material's constant-slot -> parameter map (see pipeline/importers/palworld/shaders.py).

usage: python tools/preshaders.py raw/shaders/<material package path> [resource index]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pipeline.importers.palworld.shaders import decode  # noqa: E402

if __name__ == "__main__":
    for e in decode(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0):
        print(f"{e['slot']:<14} {e['expr']}")
