"""Bounded ZIP extraction for manually supplied project update packages."""
import argparse
import stat
import zipfile
from pathlib import Path, PurePosixPath


MAX_FILES = 5000
MAX_UNCOMPRESSED = 512 * 1024 * 1024


def extract_zip(archive, destination):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > MAX_FILES or sum(item.file_size for item in entries) > MAX_UNCOMPRESSED:
            raise ValueError("package exceeds extraction limits")
        seen = set()
        for item in entries:
            name = item.filename
            posix = PurePosixPath(name)
            if (not name or "\\" in name or posix.is_absolute()
                    or any(part in {"..", ""} for part in posix.parts)
                    or (posix.parts and ":" in posix.parts[0])):
                raise ValueError("package contains an unsafe path")
            target = (root / Path(*posix.parts)).resolve()
            if target != root and root not in target.parents:
                raise ValueError("package path escapes extraction directory")
            key = str(target).casefold()
            if key in seen:
                raise ValueError("package contains duplicate paths")
            seen.add(key)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("package contains a symbolic link")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with package.open(item) as source, target.open("xb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > item.file_size or written > MAX_UNCOMPRESSED:
                        raise ValueError("package entry exceeded declared size")
                    output.write(chunk)
            if written != item.file_size:
                raise ValueError("package entry size mismatch")
    return root


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    parser.add_argument("destination")
    args = parser.parse_args()
    extract_zip(args.archive, args.destination)


if __name__ == "__main__":
    main()
