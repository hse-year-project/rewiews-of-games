import os
from pathlib import Path


def replace(tmp: Path, path: Path):
    """Move a fully written temporary file to `path`, flushed to disk first, so a crash or power
    loss never leaves an empty or half-written file under the final name."""
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
