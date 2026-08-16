#!/usr/bin/env python3
"""Verify that every 64-bit ELF in an APK supports Android 16 KB pages."""

from __future__ import annotations

import argparse
import struct
import sys
import zipfile


PAGE_SIZE = 16 * 1024
PT_LOAD = 1


def load_alignments(payload: bytes) -> list[int]:
    if payload[:4] != b"\x7fELF" or payload[4] != 2 or payload[5] != 1:
        raise ValueError("expected a little-endian ELF64 library")
    phoff = struct.unpack_from("<Q", payload, 32)[0]
    phentsize = struct.unpack_from("<H", payload, 54)[0]
    phnum = struct.unpack_from("<H", payload, 56)[0]
    alignments = []
    for index in range(phnum):
        header = phoff + index * phentsize
        if struct.unpack_from("<I", payload, header)[0] == PT_LOAD:
            alignments.append(struct.unpack_from("<Q", payload, header + 48)[0])
    return alignments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", help="APK to inspect")
    args = parser.parse_args()
    failures = []
    with zipfile.ZipFile(args.apk) as apk:
        libraries = sorted(name for name in apk.namelist() if name.endswith(".so"))
        if not libraries:
            parser.error("APK contains no native libraries")
        for name in libraries:
            alignments = load_alignments(apk.read(name))
            minimum = min(alignments, default=0)
            verdict = "OK" if minimum >= PAGE_SIZE else "FAIL"
            print(f"{verdict} {name}: minimum PT_LOAD alignment = {minimum:#x}")
            if minimum < PAGE_SIZE:
                failures.append(name)
    if failures:
        print(f"{len(failures)} incompatible native libraries", file=sys.stderr)
        return 1
    print(f"All {len(libraries)} native libraries support {PAGE_SIZE // 1024} KB pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
