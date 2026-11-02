"""Authenticated SSH client for the desktop's root-owned power guard."""

import asyncio
import json
import re


class PowerClientError(Exception):
    pass


class PowerClient:
    def __init__(self, host: str, user: str, key: str, known_hosts: str):
        if not host or not user or not key or not known_hosts:
            raise ValueError("desktop SSH identity and known-hosts file are required")
        self.host, self.user, self.key, self.known_hosts = host, user, key, known_hosts

    async def call(self, operation: str, epoch: int, owner: str, anchor: str) -> dict:
        if (
            operation not in ("fence", "suspend") or type(epoch) is not int or epoch <= 0
            or not re.fullmatch(r"[0-9a-f]{32}", owner)
            or not re.fullmatch(r"[0-9a-f-]{36}", anchor)
        ):
            raise ValueError("invalid power guard request")
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-i", self.key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", "ConnectTimeout=10", f"{self.user}@{self.host}",
            "sudo", "-n", "/usr/local/sbin/dark-factory-power-guard",
            operation, str(epoch), owner, anchor,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=40)
        except TimeoutError as exc:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise PowerClientError("power guard request timed out") from exc
        if proc.returncode != 0:
            raise PowerClientError("power guard request failed or is unresolved")
        try:
            state = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PowerClientError("invalid power guard response") from exc
        expected_phase = "ready" if operation == "fence" else "suspending"
        if not isinstance(state, dict) or state != {
            "epoch": epoch, "owner": owner, "anchor": anchor, "phase": expected_phase,
        }:
            raise PowerClientError("power guard response did not confirm the requested fence")
        return state
