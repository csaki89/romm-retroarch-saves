import hashlib
import zipfile
from pathlib import Path


def compute_content_hash(file_path):
    """MD5 content hash, byte-compatible with RomM's server-side save hash.

    Plain files: MD5 of the raw bytes. Zip files: MD5 of the sorted
    "name:md5(content)" lines joined by "\\n" (directories skipped).
    """
    file_path = Path(file_path)
    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path, "r") as zf:
            lines = []
            for name in sorted(zf.namelist()):
                if name.endswith("/"):
                    continue
                lines.append(f"{name}:{hashlib.md5(zf.read(name)).hexdigest()}")
        return hashlib.md5("\n".join(lines).encode()).hexdigest()

    digest = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            digest.update(chunk)
    return digest.hexdigest()
