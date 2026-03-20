import logging
import socket

log = logging.getLogger("wol-proxy")


def send_wol_packet(mac: str, broadcast: str, name: str) -> None:
    """Send a Wake-on-LAN magic packet to the given MAC address."""
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, 9))
    log.info("[%s] Sent WoL packet to %s", name, mac)
