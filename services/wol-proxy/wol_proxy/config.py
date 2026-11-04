from dataclasses import dataclass, field

import yaml


@dataclass
class BackendConfig:
    """Port-based backend (existing, e.g. Ollama)."""
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
class PathRoute:
    """Route a path prefix to a different target."""
    prefix: str
    target: str

    @property
    def target_host(self) -> str:
        return self.target.rsplit(":", 1)[0]

    @property
    def target_port(self) -> int:
        return int(self.target.rsplit(":", 1)[1])


@dataclass
class NodeGroupBackend:
    """A single app behind a NodeGroup, routed by hostname."""
    hostname: str
    target: str  # e.g. "paperless.paperless.svc.cluster.local:8000"
    path_routes: list[PathRoute] = field(default_factory=list)

    @property
    def target_host(self) -> str:
        return self.target.rsplit(":", 1)[0]

    @property
    def target_port(self) -> int:
        return int(self.target.rsplit(":", 1)[1])

    def resolve_target(self, path: str) -> tuple[str, int]:
        """Return (host, port) for the given request path."""
        for route in self.path_routes:
            if path.startswith(route.prefix):
                return route.target_host, route.target_port
        return self.target_host, self.target_port


@dataclass
class SandboxWorkerConfig:
    node: str
    suspend_host: str
    wol_mac: str
    wol_host: str
    wol_broadcast: str
    power_key_path: str
    known_hosts_path: str
    power_ssh_user: str = "dark-factory-power"


@dataclass
class SandboxPoolConfig:
    workers: list[SandboxWorkerConfig]
    backends: list[str]


@dataclass
class NodeGroupConfig:
    """Host-based backend group sharing one physical node."""
    name: str
    listen_port: int
    wol_mac: str
    wol_host: str = ""
    wol_broadcast: str = "192.168.2.255"
    suspend_host: str = ""
    ssh_user: str = ""
    ssh_key_path: str = "/secrets/ssh-key"
    idle_timeout_minutes: int = 30
    wake_timeout_seconds: int = 120
    health_check_host: str = ""  # Any backend to TCP-check for node liveness
    backends: list[NodeGroupBackend] = field(default_factory=list)
    sandbox_namespace: str = ""
    sandbox_instance: str = ""
    sandbox_workspace_storage_class: str = ""
    sandbox_workspace_delete_actor: str = ""
    kubernetes_node: str = ""
    sandbox_admission_enabled: bool = False
    sandbox_power_interlock_enabled: bool = False
    power_ssh_user: str = "dark-factory-power"
    sandbox_pool: SandboxPoolConfig | None = None


@dataclass
class Config:
    backends: list[BackendConfig] = field(default_factory=list)
    node_groups: list[NodeGroupConfig] = field(default_factory=list)


def optional_boolean(config: dict, name: str) -> bool:
    value = config.get(name, False)
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


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

    node_groups = []
    for ng in raw.get("nodeGroups", []):
        pool = ng.get("sandboxPool")
        if pool is not None:
            if not isinstance(pool, dict) or not isinstance(pool.get("workers"), list) or not isinstance(pool.get("backends"), list):
                raise ValueError("sandboxPool requires worker and backend lists")
            workers = []
            for worker in pool["workers"]:
                if not isinstance(worker, dict):
                    raise ValueError("invalid sandbox worker")
                workers.append(SandboxWorkerConfig(
                    node=worker["node"], suspend_host=worker["suspendHost"], wol_mac=worker["wolMac"],
                    wol_host=worker["wolHost"], wol_broadcast=worker["wolBroadcast"],
                    power_key_path=worker["powerKeyPath"], known_hosts_path=worker["knownHostsPath"],
                    power_ssh_user=worker.get("powerSshUser", "dark-factory-power"),
                ))
            pool = SandboxPoolConfig(workers=workers, backends=pool["backends"])
        ng_backends = []
        for nb in ng.get("backends", []):
            path_routes = [
                PathRoute(prefix=pr["prefix"], target=pr["target"])
                for pr in nb.get("pathRoutes", [])
            ]
            ng_backends.append(
                NodeGroupBackend(
                    hostname=nb["hostname"],
                    target=nb["target"],
                    path_routes=path_routes,
                )
            )
        node_groups.append(
            NodeGroupConfig(
                name=ng["name"],
                listen_port=ng["listenPort"],
                wol_mac=ng["wolMac"],
                wol_host=ng.get("wolHost", ""),
                wol_broadcast=ng.get("wolBroadcast", "192.168.2.255"),
                suspend_host=ng.get("suspendHost", ""),
                ssh_user=ng.get("sshUser", ""),
                ssh_key_path=ng.get("sshKeyPath", "/secrets/ssh-key"),
                idle_timeout_minutes=ng.get("idleTimeoutMinutes", 30),
                wake_timeout_seconds=ng.get("wakeTimeoutSeconds", 120),
                health_check_host=ng.get("healthCheckHost", ""),
                backends=ng_backends,
                sandbox_namespace=ng.get("sandboxNamespace", ""),
                sandbox_instance=ng.get("sandboxInstance", ""),
                sandbox_workspace_storage_class=ng.get("sandboxWorkspaceStorageClass", ""),
                sandbox_workspace_delete_actor=ng.get("sandboxWorkspaceDeleteActor", ""),
                kubernetes_node=ng.get("kubernetesNode", ""),
                sandbox_admission_enabled=optional_boolean(ng, "sandboxAdmissionEnabled"),
                sandbox_power_interlock_enabled=optional_boolean(ng, "sandboxPowerInterlockEnabled"),
                power_ssh_user=ng.get("powerSshUser", "dark-factory-power"),
                sandbox_pool=pool,
            )
        )

    return Config(backends=backends, node_groups=node_groups)
