import asyncio
import os
import sys
import unittest
from unittest.mock import patch

import main


class MainEnvDefaultsTest(unittest.TestCase):
    def tearDown(self):
        main._REPO_ENV_CACHE = None

    def test_get_env_or_default_prefers_process_env(self):
        main._REPO_ENV_CACHE = {"VITE_BACKEND_PORT": "7000"}
        with patch.dict(os.environ, {"VITE_BACKEND_PORT": "9000"}, clear=False):
            self.assertEqual(main._get_env_or_default("VITE_BACKEND_PORT", "8000"), "9000")

    def test_get_default_port_reads_repo_env_cache(self):
        main._REPO_ENV_CACHE = {"VITE_BACKEND_PORT": "7000"}
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main._get_default_port(), 7000)

    def test_get_default_host_reads_repo_env_cache(self):
        main._REPO_ENV_CACHE = {"VITE_BACKEND_HOST": "0.0.0.0"}
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main._get_default_host(), "0.0.0.0")


@unittest.skipUnless(
    sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"),
    "Windows-only event loop policy behavior",
)
class MainWindowsEventLoopPolicyTest(unittest.TestCase):
    def test_windows_policy_helper_sets_proactor_for_async_subprocesses(self):
        previous_policy = asyncio.get_event_loop_policy()
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            main._ensure_windows_asyncio_subprocess_policy()
            self.assertIsInstance(
                asyncio.get_event_loop_policy(),
                asyncio.WindowsProactorEventLoopPolicy,
            )
        finally:
            asyncio.set_event_loop_policy(previous_policy)


if __name__ == "__main__":
    unittest.main()
