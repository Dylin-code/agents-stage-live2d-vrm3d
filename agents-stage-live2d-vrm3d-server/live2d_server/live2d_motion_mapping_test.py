"""Tests for the Live2D motion-mapping warmup gating + abort logic."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from live2d_server import live2d_motion_mapping as mapping


class WarmupGatingTest(unittest.TestCase):
    def setUp(self) -> None:
        # Avoid leaking state between tests.
        mapping._warmup_started = False

    def tearDown(self) -> None:
        mapping._warmup_started = False

    def test_disabled_by_default(self) -> None:
        """No env → start_live2d_motion_mapping_warmup must not spawn the
        background thread. This protects user CPU/quota since the codex
        CLI prompt frequently hits idle timeout and burns hours of subprocess
        time for no useful cache fill."""
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("LIVE2D_MOTION_MAPPING_WARMUP_ENABLED", None)
            with patch("threading.Thread") as thread_mock:
                mapping.start_live2d_motion_mapping_warmup()
        thread_mock.assert_not_called()
        self.assertFalse(mapping._warmup_started)

    def test_explicit_enable_starts_thread(self) -> None:
        with patch.dict(
            "os.environ",
            {"LIVE2D_MOTION_MAPPING_WARMUP_ENABLED": "1"},
            clear=False,
        ):
            with patch("threading.Thread") as thread_mock:
                mapping.start_live2d_motion_mapping_warmup()
        thread_mock.assert_called_once()
        self.assertTrue(mapping._warmup_started)

    def test_force_bypasses_env_gate(self) -> None:
        """``force=True`` is used by the /api/settings reload path — it
        must still kick off the thread even when warmup is disabled in env."""
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop("LIVE2D_MOTION_MAPPING_WARMUP_ENABLED", None)
            with patch("threading.Thread") as thread_mock:
                mapping.start_live2d_motion_mapping_warmup(force=True)
        thread_mock.assert_called_once()

    def test_does_not_restart_when_already_running(self) -> None:
        with patch.dict(
            "os.environ",
            {"LIVE2D_MOTION_MAPPING_WARMUP_ENABLED": "1"},
            clear=False,
        ):
            mapping._warmup_started = True
            with patch("threading.Thread") as thread_mock:
                mapping.start_live2d_motion_mapping_warmup()
        thread_mock.assert_not_called()


class WarmupEnabledHelperTest(unittest.TestCase):
    def test_truthy_values(self) -> None:
        for value in ("1", "true", "yes", "ON", "True"):
            with patch.dict(
                "os.environ",
                {"LIVE2D_MOTION_MAPPING_WARMUP_ENABLED": value},
                clear=False,
            ):
                self.assertTrue(mapping._warmup_enabled(), f"value={value!r}")

    def test_falsy_values(self) -> None:
        for value in ("", "0", "false", "no", "off"):
            with patch.dict(
                "os.environ",
                {"LIVE2D_MOTION_MAPPING_WARMUP_ENABLED": value},
                clear=False,
            ):
                self.assertFalse(mapping._warmup_enabled(), f"value={value!r}")


if __name__ == "__main__":
    unittest.main()
