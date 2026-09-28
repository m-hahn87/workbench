from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class RepositoryLockTimeout(TimeoutError):
    pass


class RepositoryLock:
    """Small cross-platform inter-process lock for the single-writer MVP."""

    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self._file: BinaryIO | None = None

    def __enter__(self) -> RepositoryLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        try:
            self._file.seek(0)
            if self._file.read(1) == b"":
                self._file.seek(0)
                self._file.write(b"0")
                self._file.flush()

            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._acquire()
                    return self
                except OSError:
                    if time.monotonic() < deadline:
                        time.sleep(0.05)
                        continue
                    raise RepositoryLockTimeout(
                        f"could not acquire repository lock {self.path}"
                    )
        except BaseException:
            if self._file is not None:
                self._file.close()
                self._file = None
            raise

    def _acquire(self) -> None:
        assert self._file is not None
        self._file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self) -> None:
        assert self._file is not None
        self._file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            try:
                self._release()
            finally:
                self._file.close()
                self._file = None

    def __del__(self) -> None:
        if self._file is None:
            return
        try:
            self._release()
        except (OSError, ValueError):
            pass
        finally:
            self._file.close()
            self._file = None
