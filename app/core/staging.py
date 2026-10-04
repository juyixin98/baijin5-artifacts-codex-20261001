"""Permission-controlled fragment staging and atomic release.

Lifecycle of plaintext bytes:

1. A segment is AEAD-verified *before* anything is written (verify-then-stage).
2. The verified fragment is written to a per-stream directory created 0700,
   in a file created 0600.  There is deliberately no API to read a fragment
   back: partial plaintext is never exposed.
3. Fragments accumulate only while every segment authenticates.  The first
   failure shreds the whole stream directory (:meth:`shred`).
4. When - and only when - every declared segment is present, authenticated and
   correctly terminated, :meth:`release` concatenates fragments into the
   release directory via a temp-file + fsync + atomic rename.  A successful
   release returns the bytes to the caller exactly once; afterwards callers
   fetch the released artifact through controlled paths.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .errors import StorageError

_DIR_MODE = 0o700
_FILE_MODE = 0o600


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _DIR_MODE)
    except OSError:
        pass


class StagingArea:
    def __init__(self, staging_dir: str | os.PathLike[str],
                 release_dir: str | os.PathLike[str]) -> None:
        self.staging_root = Path(staging_dir)
        self.release_root = Path(release_dir)
        _ensure_dir(self.staging_root)
        _ensure_dir(self.release_root)

    def _stream_dir(self, message_id: str) -> Path:
        # message_id is charset-validated upstream; keep it filesystem-safe.
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in message_id)
        d = self.staging_root / safe
        _ensure_dir(d)
        return d

    def stage(self, message_id: str, seqno: int, plaintext: bytes) -> str:
        """Write one verified fragment 0600; return its absolute path."""
        d = self._stream_dir(message_id)
        dest = d / f"seg-{seqno:012d}.part"
        try:
            fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         _FILE_MODE)
            try:
                os.write(fd, plaintext)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(dest, _FILE_MODE)
        except OSError as exc:
            raise StorageError("failed to stage fragment",
                               seqno=seqno) from exc
        return str(dest)

    def stage_exists(self, message_id: str, seqno: int) -> bool:
        return (self._stream_dir(message_id)
                / f"seg-{seqno:012d}.part").exists()

    def shred(self, message_id: str) -> None:
        """Destroy all fragments of a stream (failure path / cleanup)."""
        d = self._stream_dir(message_id)
        try:
            for child in d.glob("*.part"):
                child.unlink(missing_ok=True)
            d.rmdir()
        except OSError as exc:  # pragma: no cover - defensive
            raise StorageError("failed to shred staging",
                               dir=str(d)) from exc

    def release(self, message_id: str, seq_count: int,
                total_len: int) -> bytes:
        """Assemble and atomically publish the complete plaintext.

        Caller guarantees all ``seq_count`` fragments exist and authenticated.
        Bytes are concatenated in order into a 0600 temp file, length checked,
        fsynced and atomically renamed.  Returns the assembled bytes.
        """
        d = self._stream_dir(message_id)
        parts = [d / f"seg-{i:012d}.part" for i in range(seq_count)]
        missing = [i for i, p in enumerate(parts) if not p.exists()]
        if missing:
            raise StorageError("release requested with missing fragments",
                               missing=missing[:10],
                               missing_count=len(missing))
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in message_id)
        final = self.release_root / f"{safe}.bin"
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=".rel-", dir=self.release_root)
        try:
            written = 0
            for p in parts:
                with open(p, "rb") as fh:
                    while True:
                        chunk = fh.read(1 << 20)
                        if not chunk:
                            break
                        os.write(tmp_fd, chunk)
                        written += len(chunk)
            os.fsync(tmp_fd)
        except OSError as exc:
            os.close(tmp_fd)
            os.unlink(tmp_name)
            raise StorageError("failed assembling release") from exc
        finally:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if written != total_len:
            os.unlink(tmp_name)
            raise StorageError("assembled length mismatch",
                               written=written, declared=total_len)
        os.chmod(tmp_name, _FILE_MODE)
        os.rename(tmp_name, final)
        self.shred(message_id)
        with open(final, "rb") as fh:
            return fh.read()

    def released_path(self, message_id: str) -> Path | None:
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in message_id)
        p = self.release_root / f"{safe}.bin"
        return p if p.exists() else None

    def read_released(self, message_id: str) -> bytes | None:
        p = self.released_path(message_id)
        return p.read_bytes() if p is not None else None
