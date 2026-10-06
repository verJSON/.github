#!/usr/bin/env python3

import argparse
import sys
import zipfile
from pathlib import Path


MAX_BYTES = 1024 * 1024
EXPECTED_NAME = "candidate-manifest.json"
EXPECTED_BUNDLE_NAME = "candidate-manifest.sigstore.json"


def extract(archive: Path, output: Path, bundle_output: Path) -> None:
    if archive.stat().st_size > MAX_BYTES:
        raise ValueError("candidate archive exceeds 1 MiB")
    with zipfile.ZipFile(archive) as value:
        entries = value.infolist()
        expected = {EXPECTED_NAME, EXPECTED_BUNDLE_NAME}
        if len(entries) != 2 or {entry.filename for entry in entries} != expected:
            raise ValueError(
                "candidate archive must contain exactly its manifest and Cosign bundle"
            )
        contents = {}
        for entry in entries:
            if entry.file_size > MAX_BYTES or entry.flag_bits & 0x1:
                raise ValueError("candidate evidence is oversized or encrypted")
            contents[entry.filename] = value.read(entry)
    output.write_bytes(contents[EXPECTED_NAME])
    bundle_output.write_bytes(contents[EXPECTED_BUNDLE_NAME])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("bundle_output", type=Path)
    args = parser.parse_args()
    try:
        extract(args.archive, args.output, args.bundle_output)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"candidate archive rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
