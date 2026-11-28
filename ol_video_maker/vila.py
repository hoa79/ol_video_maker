"""Viloläge: håll datorn vaken medan en video skapas, och märk om den ändå har sovit.

Bara ctypes och Windows egna funktioner, inga nya beroenden. På andra system gör
keep_awake ingenting, och klockorna faller tillbaka på time.monotonic.
"""

import contextlib
import ctypes
import os
import time
from typing import Iterator

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001  # datorn får inte somna (skärmen får släckas)


def _kernel32():
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.kernel32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def _set_state(flags: int) -> bool:
    k = _kernel32()
    if k is None:
        return False
    try:
        fn = k.SetThreadExecutionState
        fn.restype = ctypes.c_uint32
        fn.argtypes = [ctypes.c_uint32]
        return fn(flags) != 0
    except (AttributeError, OSError):
        return False


@contextlib.contextmanager
def keep_awake() -> Iterator[bool]:
    """Hindra viloläge så länge blocket körs. Ger True om Windows tog emot begäran.

    Gäller tråden som anropar, så blocket måste börja och sluta i samma tråd. Det hindrar
    inte viloläge när man fäller ner locket eller trycker på strömknappen.
    """
    ok = _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield ok
    finally:
        if ok:
            _set_state(ES_CONTINUOUS)  # tillbaka till det vanliga


def awake_time() -> float:
    """Sekunder som bara räknas när datorn är vaken. Tid i viloläge räknas inte."""
    k = _kernel32()
    if k is not None:
        try:
            t = ctypes.c_ulonglong(0)
            if k.QueryUnbiasedInterruptTime(ctypes.byref(t)):
                return t.value / 10_000_000  # enheten är 100 ns
        except (AttributeError, OSError):
            pass
    # Linux och macOS: time.monotonic står still medan datorn sover.
    return time.monotonic()


def total_time() -> float:
    """Sekunder som räknas hela tiden, även när datorn sover."""
    k = _kernel32()
    if k is not None:
        try:
            fn = k.GetTickCount64
            fn.restype = ctypes.c_ulonglong
            return fn() / 1000
        except (AttributeError, OSError):
            pass
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()  # okänt system: då märks ingen sovtid


class SleepMeter:
    """Mäter hur länge datorn har sovit sedan mätaren skapades."""

    def __init__(self) -> None:
        self._awake = awake_time()
        self._total = total_time()

    def slept(self) -> float:
        d = (total_time() - self._total) - (awake_time() - self._awake)
        return d if d > 2.0 else 0.0  # små skillnader är bara klockornas upplösning

    def awake(self) -> float:
        """Sekunder som datorn har varit vaken sedan start."""
        return awake_time() - self._awake
