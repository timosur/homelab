import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from wol_proxy.coder_trust import sync_trust


class CoderTrustTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_public_ca_is_published_and_existing_data_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / "ca.crt"
            ca.write_text("-----BEGIN CERTIFICATE-----\npublic-ca\n-----END CERTIFICATE-----\n")
            client = type("Client", (), {})()
            client._get = AsyncMock(return_value={"metadata": {"resourceVersion": "3"}, "data": {"other": "keep"}})
            client._put = AsyncMock()
            await sync_trust(client, "coder", ca)
            path, obj = client._put.await_args.args
            self.assertEqual(path, "/api/v1/namespaces/coder/configmaps/coder-power-trust")
            self.assertEqual(obj["data"]["other"], "keep")
            self.assertEqual(obj["data"]["ca.crt"], ca.read_text())
            ca.write_text("-----BEGIN CERTIFICATE-----\n-----BEGIN PRIVATE KEY-----")
            client._put.reset_mock()
            with self.assertRaises(ValueError):
                await sync_trust(client, "coder", ca)
            client._put.assert_not_awaited()
