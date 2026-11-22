"""Diagnostic utility for cross-runtime async and socket diagnostics.

Collects system, event loop, socketpair, and minimal asyncio execution diagnostics.
Kept strictly separate from the product's runtime code.
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import platform
import socket
import sys
import threading
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)

# Add project root and src to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def run_diagnostics() -> int:
    print("=" * 70)
    print("CS2 Trading Sniper - Async & Runtime Diagnostics")
    print("=" * 70)

    # 1. Python executable and version
    print(f"Python Executable : {sys.executable}")
    print(f"Python Version    : {sys.version}")

    # 2. OS version and architecture
    print(f"OS Platform       : {platform.platform()}")
    print(f"OS System/Release : {platform.system()} {platform.release()} (Version: {platform.version()})")
    print(f"Architecture      : {platform.machine()} ({platform.architecture()[0]}, {platform.architecture()[1]})")

    # 3. Event loop implementation and policy
    try:
        policy = asyncio.get_event_loop_policy()
        policy_name = f"{policy.__class__.__module__}.{policy.__class__.__name__}"
    except Exception as e:
        policy_name = f"Error: {e}"

    try:
        temp_loop = asyncio.new_event_loop()
        loop_name = f"{temp_loop.__class__.__module__}.{temp_loop.__class__.__name__}"
        temp_loop.close()
    except Exception as e:
        loop_name = f"Error: {e}"

    print(f"Event Loop Policy : {policy_name}")
    print(f"Event Loop Class  : {loop_name}")
    print("-" * 70)

    # Enable faulthandler with thread-based watchdog for hang detection
    faulthandler.enable()

    # 4. Test socket.socketpair() independently
    print("[1/3] Testing socket.socketpair() independently...")
    t0 = time.monotonic()
    socketpair_ok = False
    try:
        # Arm watchdog for 5 seconds
        def watchdog_socketpair() -> None:
            time.sleep(5.0)
            if not socketpair_ok:
                print("\n[WATCHDOG TRIGGERED] socketpair() hung after 5s! Stack dump:\n", file=sys.stderr)
                faulthandler.dump_traceback(file=sys.stderr)

        th = threading.Thread(target=watchdog_socketpair, daemon=True)
        th.start()

        s1, s2 = socket.socketpair()
        s1.sendall(b"PING")
        received = s2.recv(4)
        s1.close()
        s2.close()
        elapsed = time.monotonic() - t0
        socketpair_ok = True
        print(f"      Result: SUCCESS (data='{received.decode()}', elapsed={elapsed:.4f}s)")
    except Exception as e:
        print(f"      Result: FAILED ({e.__class__.__name__}: {e})")

    # 5. Test minimal asyncio.run(asyncio.sleep(0))
    print("[2/3] Testing minimal asyncio.run(asyncio.sleep(0))...")
    t0 = time.monotonic()
    asyncio_ok = False
    try:
        def watchdog_asyncio() -> None:
            time.sleep(5.0)
            if not asyncio_ok:
                print("\n[WATCHDOG TRIGGERED] asyncio.run(sleep(0)) hung after 5s! Stack dump:\n", file=sys.stderr)
                faulthandler.dump_traceback(file=sys.stderr)

        th = threading.Thread(target=watchdog_asyncio, daemon=True)
        th.start()

        asyncio.run(asyncio.sleep(0))
        elapsed = time.monotonic() - t0
        asyncio_ok = True
        print(f"      Result: SUCCESS (elapsed={elapsed:.4f}s)")
    except Exception as e:
        print(f"      Result: FAILED ({e.__class__.__name__}: {e})")

    # 6. Test specific async unit test cases
    print("[3/3] Testing reported hang test cases...")
    import io
    import unittest
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTest(loader.loadTestsFromName("tests.test_http_client.TestHttpClient.test_concurrent_request_strict_serialization"))
    suite.addTest(loader.loadTestsFromName("tests.test_rate_limiter.TestRateLimiter.test_403_permanent_circuit_breaker"))

    buffer = io.StringIO()
    runner = unittest.TextTestRunner(stream=buffer, verbosity=1)
    t0 = time.monotonic()
    test_result = runner.run(suite)
    elapsed = time.monotonic() - t0
    print(f"      Targeted Tests: {'SUCCESS' if test_result.wasSuccessful() else 'FAILED'} (elapsed={elapsed:.4f}s, tests_run={test_result.testsRun})")

    print("=" * 70)
    if socketpair_ok and asyncio_ok and test_result.wasSuccessful():
        print("Diagnostic summary: All async operations completed cleanly without deadlock.")
        return 0
    else:
        print("Diagnostic summary: Deadlock or failure detected in runtime environment.")
        return 1


if __name__ == "__main__":
    sys.exit(run_diagnostics())
