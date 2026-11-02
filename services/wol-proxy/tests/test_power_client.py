import json
import unittest
from unittest.mock import AsyncMock, patch

from wol_proxy.power_client import PowerClient, PowerClientError

OWNER = "a" * 32
ANCHOR = "12345678-1234-1234-1234-123456789abc"


class PowerClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fence_requires_verified_host_key_and_matching_response(self):
        client = PowerClient("desktop", "user", "/key", "/known-hosts")
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (json.dumps({
            "epoch": 2, "owner": OWNER, "anchor": ANCHOR, "phase": "ready",
        }).encode(), b"")
        with patch("wol_proxy.power_client.asyncio.create_subprocess_exec", return_value=proc) as spawn:
            await client.call("fence", 2, OWNER, ANCHOR)
        args = spawn.await_args.args
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("UserKnownHostsFile=/known-hosts", args)
        self.assertEqual(args[-4:], ("fence", "2", OWNER, ANCHOR))

    async def test_fence_rejects_mismatched_response(self):
        client = PowerClient("desktop", "user", "/key", "/known-hosts")
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b'{}', b"")
        with patch("wol_proxy.power_client.asyncio.create_subprocess_exec", return_value=proc):
            with self.assertRaises(PowerClientError):
                await client.call("fence", 2, OWNER, ANCHOR)
