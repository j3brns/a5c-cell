#!/usr/bin/env python3
"""
test_install_idempotency.py — Verification for TASK-057.
"""

import subprocess
import sys
import time


def run_installer():
    start = time.time()
    result = subprocess.run(
        ["uv", "run", "python", "scripts/install_tools.py"], capture_output=True, text=True
    )
    end = time.time()
    return result, end - start


def main():
    print("Running first pass (may download/install)...")
    res1, duration1 = run_installer()
    if res1.returncode != 0:
        print(f"First pass failed:\n{res1.stderr}")
        sys.exit(1)
    print(f"First pass took {duration1:.2f}s")

    print("\nRunning second pass (idempotency check)...")
    res2, duration2 = run_installer()
    if res2.returncode != 0:
        print(f"Second pass failed:\n{res2.stderr}")
        sys.exit(1)
    print(f"Second pass took {duration2:.2f}s")

    # Check that second pass is significantly faster (should be < 2s usually if skipping)
    # or at least that it didn't log any "Downloading" or "Extracting" lines.
    if "Downloading" in res2.stdout or "Extracting" in res2.stdout:
        print("FAIL: Second pass performed download or extraction!")
        sys.exit(1)

    print("\nSUCCESS: Idempotency verified.")


if __name__ == "__main__":
    main()
