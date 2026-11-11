"""Publish only the admission CA, not its private key, for the Coder provisioner."""

import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

from aiohttp import ClientError

log = logging.getLogger("wol-proxy")


async def sync_trust(client, namespace, ca_path):
    ca = Path(ca_path).read_text()
    if "-----BEGIN CERTIFICATE-----" not in ca or "PRIVATE KEY" in ca:
        raise ValueError("invalid admission CA")
    path = f"/api/v1/namespaces/{quote(namespace, safe='')}/configmaps/coder-power-trust"
    current = await client._get(path)
    metadata, data = current.get("metadata"), current.get("data", {})
    if not isinstance(metadata, dict) or not metadata.get("resourceVersion") or not isinstance(data, dict):
        raise ValueError("invalid Coder trust ConfigMap")
    if data.get("ca.crt") != ca:
        await client._put(path, {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata,
                                 "data": {**data, "ca.crt": ca}})


async def publish_trust(client, namespace, ca_path):
    while True:
        try:
            await sync_trust(client, namespace, ca_path)
        except (ClientError, OSError, ValueError, KeyError, TimeoutError) as exc:
            log.warning("Cannot synchronize Coder preflight trust: %s", type(exc).__name__)
        await asyncio.sleep(60)
