from dataclasses import dataclass, field

import yaml


@dataclass
class BackendConfig:
    name: str
    listen_port: int
    target_host: str
    target_port: int
    wol_mac: str
    wol_host: str = ""
    wol_broadcast: str = "192.168.2.255"
    idle_timeout_minutes: int = 30
    wake_timeout_seconds: int = 120
    ssh_user: str = ""
    ssh_key_path: str = "/secrets/ssh-key"
    cached_paths: list[str] = field(default_factory=list)
    cached_path_defaults: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    backends: list[BackendConfig] = field(default_factory=list)


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    backends = []
    for b in raw.get("backends", []):
        backends.append(
            BackendConfig(
                name=b["name"],
                listen_port=b["listenPort"],
                target_host=b["targetHost"],
                target_port=b["targetPort"],
                wol_mac=b["wolMac"],
                wol_host=b.get("wolHost", ""),
                wol_broadcast=b.get("wolBroadcast", "192.168.2.255"),
                idle_timeout_minutes=b.get("idleTimeoutMinutes", 30),
                wake_timeout_seconds=b.get("wakeTimeoutSeconds", 120),
                ssh_user=b.get("sshUser", ""),
                ssh_key_path=b.get("sshKeyPath", "/secrets/ssh-key"),
                cached_paths=b.get("cachedPaths", []),
                cached_path_defaults=b.get("cachedPathDefaults", {}),
            )
        )

    return Config(backends=backends)
