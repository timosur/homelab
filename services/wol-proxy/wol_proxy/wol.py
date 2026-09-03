import asyncio
import logging
import subprocess

log = logging.getLogger("wol-proxy")
SSH_COMMAND_TIMEOUT_SECONDS = 15


async def send_wol_packet(
    mac: str,
    name: str,
    wol_host: str,
    ssh_user: str,
    ssh_key_path: str,
    broadcast: str = "192.168.2.255",
) -> None:
    """Send a Wake-on-LAN magic packet by SSHing to the host and running wakeonlan."""
    log.info("[%s] Sending WoL for %s via SSH to %s@%s", name, mac, ssh_user, wol_host)
    proc = await asyncio.create_subprocess_exec(
        "ssh",
        "-i",
        ssh_key_path,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        f"{ssh_user}@{wol_host}",
        "wakeonlan",
        "-i",
        broadcast,
        mac,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(
            proc.communicate(), timeout=SSH_COMMAND_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise RuntimeError("Wake-on-LAN SSH command timed out") from exc
    if proc.returncode == 0:
        log.info("[%s] Sent WoL packet to %s", name, mac)
    else:
        raise RuntimeError(
            f"Wake-on-LAN command failed (rc={proc.returncode}): "
            f"{output.decode(errors='replace')}"
        )
