import asyncio
import logging
import subprocess

log = logging.getLogger("wol-proxy")


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
    output, _ = await proc.communicate()
    if proc.returncode == 0:
        log.info("[%s] Sent WoL packet to %s", name, mac)
    else:
        log.error(
            "[%s] WoL failed (rc=%s): %s",
            name,
            proc.returncode,
            output.decode(errors="replace"),
        )
