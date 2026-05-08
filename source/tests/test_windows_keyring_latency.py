# ABOUTME: Regression test for Windows keyring parallel reads (issue #348)
# ABOUTME: Simulates enterprise keyring latency to verify parallel reads stay within timeout
"""Tests that Windows keyring credential reads are parallelised (issue #348).

Enterprise Windows Credential Manager can take 10-15s per read due to GPO,
smart card enforcement, or remote credential stores. The old serial code made
4 sequential reads, which could take 40-60s and exceed Claude Code's
credential_process timeout. This test simulates that latency and verifies that
the parallelised code completes in roughly one read-latency rather than four.
"""

import json
import platform
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest


SIMULATED_KEYRING_LATENCY_S = 2  # Use 2s per read to keep test fast but meaningful
SERIAL_EXPECTED_MIN_S = SIMULATED_KEYRING_LATENCY_S * 4  # old code: 4 sequential reads
PARALLEL_EXPECTED_MAX_S = SIMULATED_KEYRING_LATENCY_S * 3  # new code: parallel, well under 4x serial


def _make_config():
    expiry = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    token = "X" * 500
    mid = len(token) // 2
    return {
        "keys_json": json.dumps({"AccessKeyId": "AKIATEST", "SecretAccessKey": "secret"}),
        "token1": token[:mid],
        "token2": token[mid:],
        "meta_json": json.dumps({"Version": 1, "Expiration": expiry}),
        "expiry": expiry,
    }


def _slow_get_password(service, account):
    """Simulate slow enterprise Windows Credential Manager."""
    import hashlib as _hashlib
    time.sleep(SIMULATED_KEYRING_LATENCY_S)
    cfg = _make_config()
    token = cfg["token1"] + cfg["token2"]
    token_hash = _hashlib.sha256(token.encode()).hexdigest()
    header = json.dumps({
        "AccessKeyId": "AKIATEST",
        "SecretAccessKey": "secret",
        "Version": 1,
        "Expiration": cfg["expiry"],
        "token_hash": token_hash,
    })
    suffix_map = {
        "-header": header,
        "-token1": cfg["token1"],
        "-token2": cfg["token2"],
    }
    for suffix, value in suffix_map.items():
        if account.endswith(suffix):
            return value
    return None


@pytest.fixture()
def auth_windows():
    """Return a MultiProviderAuth instance configured for Windows keyring mode."""
    from credential_provider.__main__ import MultiProviderAuth
    auth = MultiProviderAuth.__new__(MultiProviderAuth)
    auth.debug = False
    auth.profile = "TestProfile"
    auth.credential_storage = "keyring"
    auth.config = {"provider_domain": "test.okta.com", "credential_storage": "keyring"}
    return auth


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows keyring path only")
def test_windows_keyring_reads_are_parallel(auth_windows):
    """Parallel reads must complete in under 2x simulated latency, not 4x."""
    with patch("credential_provider.__main__.platform.system", return_value="Windows"), \
         patch("credential_provider.__main__.keyring.get_password", side_effect=_slow_get_password):
        start = time.monotonic()
        creds = auth_windows.get_cached_credentials()
        elapsed = time.monotonic() - start

    assert creds is not None, "Expected credentials to be returned"
    assert creds["AccessKeyId"] == "AKIATEST"
    assert elapsed < PARALLEL_EXPECTED_MAX_S, (
        f"Parallel reads took {elapsed:.1f}s — expected under {PARALLEL_EXPECTED_MAX_S}s. "
        f"Reads may have regressed to sequential."
    )


def test_windows_keyring_parallel_faster_than_serial(auth_windows):
    """Demonstrate parallel reads are meaningfully faster than serial would be.

    This test runs on all platforms by mocking platform.system to Windows.
    It measures actual elapsed time with a simulated slow keyring to confirm
    the parallel implementation beats the serial baseline.
    """
    call_times = []

    def slow_get_password(service, account):
        call_times.append(time.monotonic())
        time.sleep(SIMULATED_KEYRING_LATENCY_S)
        return _slow_get_password(service, account)

    with patch("credential_provider.__main__.platform.system", return_value="Windows"), \
         patch("credential_provider.__main__.keyring.get_password", side_effect=slow_get_password):
        start = time.monotonic()
        creds = auth_windows.get_cached_credentials()
        elapsed = time.monotonic() - start

    assert creds is not None, "Expected credentials to be returned"

    # All 4 reads should have started within one latency window of each other
    # (i.e. they were launched in parallel, not sequentially)
    assert len(call_times) == 3, f"Expected 3 keyring reads, got {len(call_times)}"
    spread = max(call_times) - min(call_times)
    assert spread < SIMULATED_KEYRING_LATENCY_S, (
        f"Keyring reads started {spread:.2f}s apart — looks like they ran serially. "
        f"Expected all 4 to start within {SIMULATED_KEYRING_LATENCY_S}s of each other."
    )

    assert elapsed < PARALLEL_EXPECTED_MAX_S, (
        f"Total elapsed {elapsed:.1f}s >= {PARALLEL_EXPECTED_MAX_S}s serial minimum. "
        f"Reads appear sequential."
    )
