"""Readable installer diagnostics without fragile shell one-liners."""
import struct
import sys

if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
    print("AssignmentHub requires 64-bit Python 3.12. Install it from python.org.")
    raise SystemExit(1)
print("Supported Python:", sys.version.split()[0], "64-bit")
