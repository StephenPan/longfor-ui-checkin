"""Keep the two Longfor channel sessions from interrupting one another."""
from contextlib import contextmanager
import msvcrt
import os
from pathlib import Path
import time

LOCK = Path(__file__).resolve().parent / "longfor-channel.lock"


@contextmanager
def channel_session(guard=lambda: None, timeout=90):
    with LOCK.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            guard()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("另一渠道仍在登录或签到，本次未能开始，请检查结果")
                time.sleep(0.5)
        try:
            guard()
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
