import unittest
from unittest.mock import AsyncMock, patch

import httpx

from live2d_server import claude_usage
from live2d_server.session_bridge_api import bridge_claude_usage


class _FailingAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        request = httpx.Request("GET", claude_usage.USAGE_API_URL)
        raise httpx.ConnectError("network unavailable", request=request)


class ClaudeUsageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        claude_usage._cache["data"] = None
        claude_usage._cache["fetched_at"] = 0.0

    async def test_fetch_claude_usage_treats_network_failure_as_unavailable(self) -> None:
        with patch("live2d_server.claude_usage._get_access_token", return_value="token"):
            with patch("live2d_server.claude_usage.httpx.AsyncClient", return_value=_FailingAsyncClient()):
                with self.assertLogs("live2d_server.claude_usage", level="WARNING") as logs:
                    result = await claude_usage.fetch_claude_usage()

        self.assertIsNone(result)
        self.assertTrue(any("Claude usage unavailable" in line for line in logs.output))
        self.assertFalse(any("Traceback" in line for line in logs.output))

    async def test_fetch_claude_usage_returns_stale_cache_on_network_failure(self) -> None:
        stale = {"five_hour": {"utilization": 12.5}}
        claude_usage._cache["data"] = stale
        claude_usage._cache["fetched_at"] = 0.0

        with patch("live2d_server.claude_usage._get_access_token", return_value="token"):
            with patch("live2d_server.claude_usage.httpx.AsyncClient", return_value=_FailingAsyncClient()):
                result = await claude_usage.fetch_claude_usage()

        self.assertEqual(result, stale)

    async def test_bridge_claude_usage_returns_empty_summary_when_usage_unavailable(self) -> None:
        with patch("live2d_server.claude_usage.fetch_claude_usage", new=AsyncMock(return_value=None)):
            result = await bridge_claude_usage()

        self.assertEqual(
            result,
            {
                "five_hour": None,
                "seven_day": None,
                "extra_usage": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
