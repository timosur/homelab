# Milestone 3: Extend WoL Proxy for Desktop Node

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `wol-proxy` with host-based HTTP routing, a JS-polling wake-up page, and global idle-timeout suspend — so desktop apps show a "waking up" page instead of 503 when the node is asleep.

**Architecture:** The existing `wol-proxy` is a port-based TCP reverse proxy for Ollama. This milestone adds a new `NodeGroup` concept: multiple app backends sharing one physical node, one WoL MAC, one global idle timer, one suspend target. A single HTTP listener (port 8080) routes by `Host` header to the correct backend K8s Service. When the node sleeps, users get an HTML page with JS polling `/wol-proxy/status` that auto-redirects when the backend becomes ready. All desktop app HTTPRoutes are redirected from their app services to the wol-proxy `desktop-proxy` Service via Gateway API.

**Tech Stack:** Python/aiohttp, K3s, Kustomize, Gateway API (HTTPRoute, ReferenceGrant), Cilium (CiliumNetworkPolicy)

**Dependencies:**
- **Milestone 2 must be complete** before end-to-end testing (node must exist to wake/suspend)
- Python code changes (Tasks 12-15) can start in parallel with Milestone 2
- K8s manifest changes (Tasks 16-17) can be prepared but only tested after Milestone 2

**Acceptance Criteria:**
- wol-proxy listens on port 8080 for host-based HTTP routing
- Wake-up page shown when desktop node is asleep
- `/wol-proxy/status` returns JSON `{"state": "sleeping|waking|ready", "node": "desktop"}`
- All desktop app HTTPRoutes point to `desktop-proxy.wol-proxy:8080`
- ReferenceGrant allows cross-namespace backendRefs
- CiliumNetworkPolicy allows Envoy Gateway → wol-proxy:8080
- 30-minute global idle timeout triggers node suspend via SSH

---

## Key Concepts

- **NodeGroup**: Multiple app backends sharing one physical node, one WoL MAC, one global idle timer
- **Host-routing**: Single HTTP listener routes by `Host` header to correct backend service
- **Wake-up page**: HTML with JS polling `/wol-proxy/status`; auto-redirects when ready
- **Global idle timer**: Any request to any desktop backend resets the timer; suspend only after ALL are idle for 30 min

---

## File Structure

### New files
- `services/wol-proxy/wol_proxy/wake_page.py` — HTML/JS template for wake-up page
- `apps/wol-proxy/service-desktop.yaml` — Service for desktop HTTP proxy port (8080)
- `apps/wol-proxy/allow-ingress-from-envoy.yaml` — CiliumNetworkPolicy allowing Envoy Gateway → wol-proxy:8080
- `apps/wol-proxy/reference-grant.yaml` — ReferenceGrant for cross-namespace HTTPRoute backendRefs

### Modified files — Python code
- `services/wol-proxy/wol_proxy/config.py` — Add `NodeGroupConfig`, `NodeGroupBackend` dataclasses + parsing
- `services/wol-proxy/wol_proxy/proxy.py` — Add `NodeGroupProxy` class with host-routing, status API, idle timer
- `services/wol-proxy/wol_proxy/server.py` — Start NodeGroup HTTP listener alongside existing backends

### Modified files — K8s manifests
- `apps/wol-proxy/configmap.yaml` — Add `nodeGroups` section with desktop backends
- `apps/wol-proxy/deployment.yaml` — Add port 8080, update resources
- `apps/wol-proxy/kustomization.yaml` — Add new resources

### Modified files — HTTPRoutes
- `networking/httproutes/home/paperless.yaml` — backendRef → wol-proxy
- `networking/httproutes/home/actual.yaml` — backendRef → wol-proxy
- `networking/httproutes/home/vinyl-manager.yaml` — backendRef → wol-proxy
- `networking/httproutes/home/kustomization.yaml` — Add `paperless.yaml` (currently orphaned)
- `networking/httproutes/internet/paperless.yaml` — backendRef → wol-proxy
- `networking/httproutes/internet/mealie.yaml` — backendRef → wol-proxy
- `networking/httproutes/internet/n8n.yaml` — backendRef → wol-proxy

---

### Task 12: Extend wol-proxy config model

**Files:**
- Modify: `services/wol-proxy/wol_proxy/config.py`

- [ ] **Step 1: Add NodeGroupConfig and NodeGroupBackend dataclasses**

```python
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
class NodeGroupBackend:
    """A single app behind a NodeGroup, routed by hostname."""
    hostname: str
    target: str  # e.g. "paperless.paperless.svc.cluster.local:8000"

    @property
    def target_host(self) -> str:
        return self.target.rsplit(":", 1)[0]

    @property
    def target_port(self) -> int:
        return int(self.target.rsplit(":", 1)[1])


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


@dataclass
class Config:
    backends: list[BackendConfig] = field(default_factory=list)
    node_groups: list[NodeGroupConfig] = field(default_factory=list)
```

Update `load_config` to parse the new `nodeGroups` section (alongside existing `backends`):

```python
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
        ng_backends = []
        for nb in ng.get("backends", []):
            ng_backends.append(
                NodeGroupBackend(
                    hostname=nb["hostname"],
                    target=nb["target"],
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
            )
        )

    return Config(backends=backends, node_groups=node_groups)
```

- [ ] **Step 2: Commit**

```bash
git add services/wol-proxy/wol_proxy/config.py
git commit -m "feat(wol-proxy): add NodeGroup config model for host-based routing"
```

---

### Task 13: Create wake-up page template

**Files:**
- Create: `services/wol-proxy/wol_proxy/wake_page.py`

- [ ] **Step 1: Create HTML template with JS polling**

The page shows a spinner and status text. JavaScript polls `GET /wol-proxy/status` every 3 seconds. Status transitions: `sleeping` → `waking` → `ready`. On `ready`, auto-redirect to the original URL.

```python
"""Wake-up page HTML template served when the desktop node is sleeping."""

WAKE_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Starting up – {app_name}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f172a; color: #e2e8f0;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh;
    }}
    .card {{
      text-align: center; max-width: 420px; padding: 2rem;
    }}
    .spinner {{
      width: 48px; height: 48px; margin: 0 auto 1.5rem;
      border: 4px solid #334155; border-top-color: #38bdf8;
      border-radius: 50%; animation: spin 1s linear infinite;
    }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    h1 {{ font-size: 1.25rem; margin-bottom: 0.5rem; }}
    #status {{ color: #94a3b8; font-size: 0.9rem; }}
    #elapsed {{ color: #64748b; font-size: 0.8rem; margin-top: 1rem; }}
    .ready .spinner {{ border-top-color: #4ade80; animation: none; }}
    .ready h1 {{ color: #4ade80; }}
  </style>
</head>
<body>
  <div class="card" id="card">
    <div class="spinner" id="spinner"></div>
    <h1 id="title">Desktop-Node wird hochgefahren…</h1>
    <p id="status">WoL-Paket gesendet. Bitte warten.</p>
    <p id="elapsed"></p>
  </div>
  <script>
    const start = Date.now();
    const poll = setInterval(async () => {{
      try {{
        const r = await fetch('/wol-proxy/status');
        const d = await r.json();
        const el = document.getElementById('status');
        const sec = Math.round((Date.now() - start) / 1000);
        document.getElementById('elapsed').textContent = sec + 's';
        if (d.state === 'ready') {{
          clearInterval(poll);
          document.getElementById('card').classList.add('ready');
          document.getElementById('title').textContent = 'Bereit!';
          el.textContent = 'Weiterleitung…';
          setTimeout(() => window.location.reload(), 500);
        }} else if (d.state === 'waking') {{
          el.textContent = 'Node startet… (' + sec + 's)';
        }} else {{
          el.textContent = 'Warte auf Aufwach-Signal…';
        }}
      }} catch (e) {{
        // ignore transient fetch failures
      }}
    }}, 3000);
  </script>
</body>
</html>
"""


def render_wake_page(app_name: str) -> str:
    return WAKE_PAGE_HTML.format(app_name=app_name)
```

- [ ] **Step 2: Commit**

```bash
git add services/wol-proxy/wol_proxy/wake_page.py
git commit -m "feat(wol-proxy): add wake-up page HTML template with JS polling"
```

---

### Task 14: Implement NodeGroupProxy class

**Files:**
- Modify: `services/wol-proxy/wol_proxy/proxy.py`

- [ ] **Step 1: Add NodeGroupProxy alongside existing ProxyBackend**

The `NodeGroupProxy` class handles:
- Host-header routing to find the correct backend
- Global idle timer across all backends in the group
- Wake-up page serving when node is asleep (HTML + status API)
- Reverse proxying when node is awake
- Suspend via SSH after idle timeout

Key methods:
- `handle_request(request)`: Main HTTP handler
  - If path is `/wol-proxy/status` → return JSON status
  - Look up backend by `Host` header
  - If node asleep → send WoL, return wake-up HTML page
  - If node awake → reverse proxy to target backend
- `check_node_health()`: TCP connect to `health_check_host` (first backend's target)
- `wake_and_wait()`: Send WoL, poll health, transition state
- `suspend()`: SSH to `suspend_host`, run `systemctl suspend`
- `idle_watcher()`: Background task, check global timer, suspend when expired

The status endpoint returns:
```json
{"state": "sleeping|waking|ready", "node": "desktop"}
```

Implementation notes:
- Reuse `send_wol_packet` from `wol.py`
- Reuse suspend logic from existing `ProxyBackend.suspend()`
- `_state` enum: `sleeping`, `waking`, `ready`
- `_wake_lock` prevents concurrent wake attempts
- Host → backend lookup via dict built at init

```python
class NodeGroupProxy:
    """Host-based HTTP proxy for a group of apps sharing one physical node."""

    def __init__(self, cfg: NodeGroupConfig) -> None:
        self.cfg = cfg
        self._last_activity = time.monotonic()
        self._state = "sleeping"  # sleeping | waking | ready
        self._wake_lock = asyncio.Lock()
        self._host_map: dict[str, NodeGroupBackend] = {
            b.hostname: b for b in cfg.backends
        }
        # Use first backend or explicit health_check_host for node liveness
        if cfg.health_check_host:
            h, p = cfg.health_check_host.rsplit(":", 1)
            self._health_host = h
            self._health_port = int(p)
        elif cfg.backends:
            self._health_host = cfg.backends[0].target_host
            self._health_port = cfg.backends[0].target_port
        else:
            self._health_host = ""
            self._health_port = 0

    async def check_node_health(self) -> bool:
        """TCP connect to any backend service to check if node is up."""
        if not self._health_host:
            return False
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self._health_host, self._health_port),
                timeout=2,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def wake_and_wait(self) -> None:
        async with self._wake_lock:
            if self._state == "ready":
                return
            self._state = "waking"
            await send_wol_packet(
                self.cfg.wol_mac,
                self.cfg.name,
                self.cfg.wol_host,
                self.cfg.ssh_user,
                self.cfg.ssh_key_path,
                self.cfg.wol_broadcast,
            )
            deadline = time.monotonic() + self.cfg.wake_timeout_seconds
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                if await self.check_node_health():
                    self._state = "ready"
                    self.touch()
                    log.info("[%s] Node is now awake", self.cfg.name)
                    return
            log.error("[%s] Node did not wake within %ds", self.cfg.name, self.cfg.wake_timeout_seconds)
            self._state = "sleeping"

    async def suspend(self) -> None:
        if not self.cfg.ssh_user or not self.cfg.suspend_host:
            return
        log.info("[%s] Suspending node via SSH to %s", self.cfg.name, self.cfg.suspend_host)
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-i", self.cfg.ssh_key_path,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"{self.cfg.ssh_user}@{self.cfg.suspend_host}",
            "sudo", "systemctl", "suspend",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        output, _ = await proc.communicate()
        if proc.returncode in (0, 255):
            log.info("[%s] Node suspended", self.cfg.name)
            self._state = "sleeping"
        else:
            log.error("[%s] Suspend failed (rc=%s): %s", self.cfg.name, proc.returncode, output.decode(errors="replace"))

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity

    async def idle_watcher(self) -> None:
        timeout = self.cfg.idle_timeout_minutes * 60
        while True:
            await asyncio.sleep(60)
            if self._state != "ready":
                continue
            if not await self.check_node_health():
                log.info("[%s] Node went offline externally", self.cfg.name)
                self._state = "sleeping"
                continue
            log.info("[%s] Idle %.0fm / %.0fm", self.cfg.name, self.idle_seconds / 60, self.cfg.idle_timeout_minutes)
            if self.idle_seconds >= timeout:
                log.info("[%s] Idle timeout reached, suspending", self.cfg.name)
                await self.suspend()

    async def handle_request(self, request: web.Request) -> web.StreamResponse:
        # Status API
        if request.path == "/wol-proxy/status":
            return web.json_response({"state": self._state, "node": self.cfg.name})

        # Find backend by Host header
        host = request.host.split(":")[0]  # strip port
        backend = self._host_map.get(host)
        if not backend:
            return web.Response(status=404, text=f"Unknown host: {host}")

        # Check node health
        if not await self.check_node_health():
            if self._state == "ready":
                self._state = "sleeping"
            # Start waking in background if not already
            if self._state == "sleeping":
                asyncio.create_task(self.wake_and_wait())
            # Return wake-up page
            from .wake_page import render_wake_page
            return web.Response(
                text=render_wake_page(backend.hostname),
                content_type="text/html",
            )

        # Node is up — proxy the request
        self._state = "ready"
        self.touch()
        target = f"http://{backend.target_host}:{backend.target_port}{request.path_qs}"
        timeout = ClientTimeout(total=None, sock_read=300)
        async with ClientSession(timeout=timeout) as session:
            async with session.request(
                method=request.method,
                url=target,
                headers={
                    k: v for k, v in request.headers.items()
                    if k.lower() not in ("host", "transfer-encoding")
                },
                data=await request.read(),
            ) as upstream:
                resp = web.StreamResponse(
                    status=upstream.status,
                    headers={
                        k: v for k, v in upstream.headers.items()
                        if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
                    },
                )
                content_length = upstream.headers.get("content-length")
                if content_length:
                    resp.content_length = int(content_length)
                try:
                    await resp.prepare(request)
                    async for chunk in upstream.content.iter_any():
                        self.touch()
                        await resp.write(chunk)
                    await resp.write_eof()
                except (ConnectionResetError, ConnectionError):
                    log.info("[%s] Client disconnected", self.cfg.name)
                return resp
```

- [ ] **Step 2: Commit**

```bash
git add services/wol-proxy/wol_proxy/proxy.py
git commit -m "feat(wol-proxy): add NodeGroupProxy with host-routing and wake-up page"
```

---

### Task 15: Start NodeGroup listener in server.py

**Files:**
- Modify: `services/wol-proxy/wol_proxy/server.py`

- [ ] **Step 1: Add NodeGroup startup alongside existing backends**

After the existing backend loop, add:

```python
    from .proxy import NodeGroupProxy

    for ng_cfg in config.node_groups:
        ng_proxy = NodeGroupProxy(ng_cfg)
        tasks.append(asyncio.create_task(ng_proxy.idle_watcher()))

        app = web.Application()
        app.router.add_route("*", "/{path_info:.*}", ng_proxy.handle_request)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", ng_cfg.listen_port)
        await site.start()
        runners.append(runner)

        hostnames = ", ".join(b.hostname for b in ng_cfg.backends)
        log.info(
            "[%s] HTTP node-group proxy listening on :%d for: %s",
            ng_cfg.name,
            ng_cfg.listen_port,
            hostnames,
        )
```

- [ ] **Step 2: Commit**

```bash
git add services/wol-proxy/wol_proxy/server.py
git commit -m "feat(wol-proxy): start NodeGroup HTTP listeners"
```

---

### Task 16: Update wol-proxy K8s manifests

**Files:**
- Modify: `apps/wol-proxy/configmap.yaml`
- Modify: `apps/wol-proxy/deployment.yaml`
- Create: `apps/wol-proxy/service-desktop.yaml`
- Create: `apps/wol-proxy/allow-ingress-from-envoy.yaml`
- Modify: `apps/wol-proxy/kustomization.yaml`

- [ ] **Step 1: Update ConfigMap with nodeGroups**

Add the `nodeGroups` section to the config.yaml in the ConfigMap. The targets use K8s service DNS — these services still exist in the app namespaces, they just have no endpoints when the node is down.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: wol-proxy-config
  namespace: wol-proxy
data:
  config.yaml: |
    backends:
      - name: ollama
        listenPort: 11434
        targetHost: 192.168.2.47
        targetPort: 11434
        wolMac: "2c:f0:5d:05:9d:80"
        wolHost: "192.168.2.100"
        wolBroadcast: "192.168.2.255"
        idleTimeoutMinutes: 30
        wakeTimeoutSeconds: 120
        sshUser: timosur
        sshKeyPath: /secrets/ssh-key
        cachedPaths:
          - /api/tags
          - /api/ps
          - /api/version
        cachedPathDefaults:
          /api/tags: '{"models":[]}'
          /api/ps: '{"models":[]}'
          /api/version: '{"version":"0.0.0"}'

    nodeGroups:
      - name: desktop
        listenPort: 8080
        wolMac: "30:9c:23:8a:30:e3"
        wolHost: "192.168.2.100"
        wolBroadcast: "192.168.2.255"
        suspendHost: "192.168.2.241"
        sshUser: timosur
        sshKeyPath: /secrets/ssh-key
        idleTimeoutMinutes: 30
        wakeTimeoutSeconds: 120
        backends:
          - hostname: "docs.home.timosur.com"
            target: "paperless.paperless.svc.cluster.local:8000"
          - hostname: "docs.timosur.com"
            target: "paperless.paperless.svc.cluster.local:8000"
          - hostname: "finance.home.timosur.com"
            target: "actual.actual.svc.cluster.local:5006"
          - hostname: "vinyl.home.timosur.com"
            target: "vinyl-frontend.vinyl-manager.svc.cluster.local:3000"
          - hostname: "mealie.timosur.com"
            target: "mealie.mealie.svc.cluster.local:9000"
          - hostname: "automate.timosur.com"
            target: "n8n.n8n.svc.cluster.local:80"
```

Note: vinyl-manager has split routing (API vs frontend). The proxy will forward the full path, so the vinyl backend service needs to be reachable too. Two options: (a) add a second hostname entry for the API, or (b) keep it simple — the frontend already proxies `/api` to the backend internally. Use option (b) and route all vinyl traffic to the frontend service.

Verify service names:
```bash
kubectl get svc -n paperless -o name
kubectl get svc -n actual -o name
kubectl get svc -n vinyl-manager -o name
kubectl get svc -n mealie -o name
kubectl get svc -n n8n -o name
```

- [ ] **Step 2: Update Deployment — add port 8080**

Add the new container port and update probes to check both ports:

```yaml
          ports:
            - containerPort: 11434
              name: ollama
            - containerPort: 8080
              name: desktop
          # ...
          livenessProbe:
            tcpSocket:
              port: 11434
            initialDelaySeconds: 5
            periodSeconds: 30
          readinessProbe:
            tcpSocket:
              port: 11434
            initialDelaySeconds: 5
            periodSeconds: 10
```

Also bump resources slightly:

```yaml
          resources:
            requests:
              cpu: 10m
              memory: 48Mi
            limits:
              cpu: 200m
              memory: 192Mi
```

- [ ] **Step 3: Create service-desktop.yaml**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: desktop-proxy
  namespace: wol-proxy
spec:
  selector:
    app: wol-proxy
  ports:
    - protocol: TCP
      port: 8080
      targetPort: 8080
      name: http
```

- [ ] **Step 4: Create allow-ingress-from-envoy.yaml**

Allow traffic from both Envoy Gateway namespaces to the wol-proxy desktop port:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-ingress-from-envoy-gateways
  namespace: wol-proxy
spec:
  endpointSelector:
    matchLabels:
      app: wol-proxy
  ingress:
    - fromEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: envoy-gateway-system
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: envoy-gateway-internet-system
      toPorts:
        - ports:
            - port: "8080"
              protocol: TCP
```

- [ ] **Step 5: Update kustomization.yaml**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - namespace.yaml
  - configmap.yaml
  - external-secret.yaml
  - deployment.yaml
  - service-ollama.yaml
  - service-desktop.yaml
  - allow-ingress-from-open-webui.yaml
  - allow-ingress-from-envoy.yaml
```

- [ ] **Step 6: Commit**

```bash
git add apps/wol-proxy/
git commit -m "feat: add desktop node-group to wol-proxy K8s manifests"
```

---

### Task 17: Redirect HTTPRoutes through wol-proxy

All desktop app HTTPRoutes change their `backendRef` from the app's service to the wol-proxy `desktop-proxy` service. This requires a `ReferenceGrant` in the `wol-proxy` namespace to allow cross-namespace backendRefs from each app namespace.

**Files:**
- Modify: `networking/httproutes/home/paperless.yaml`
- Modify: `networking/httproutes/home/actual.yaml`
- Modify: `networking/httproutes/home/vinyl-manager.yaml`
- Modify: `networking/httproutes/home/kustomization.yaml`
- Modify: `networking/httproutes/internet/paperless.yaml`
- Modify: `networking/httproutes/internet/mealie.yaml`
- Modify: `networking/httproutes/internet/n8n.yaml`
- Create: `apps/wol-proxy/reference-grant.yaml`

- [ ] **Step 1: Create ReferenceGrant in wol-proxy namespace**

This allows HTTPRoutes from any namespace to reference the `desktop-proxy` Service in the `wol-proxy` namespace:

```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-desktop-httproutes
  namespace: wol-proxy
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: paperless
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: actual
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: vinyl-manager
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: mealie
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: n8n
  to:
    - group: ""
      kind: Service
      name: desktop-proxy
```

Add `reference-grant.yaml` to `apps/wol-proxy/kustomization.yaml`.

- [ ] **Step 2: Update home HTTPRoutes**

**paperless** (`networking/httproutes/home/paperless.yaml`):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: paperless
  namespace: paperless
spec:
  parentRefs:
    - name: envoy-gateway-home
      namespace: envoy-gateway-system
  hostnames:
    - "docs.home.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: desktop-proxy
          namespace: wol-proxy
          port: 8080
```

**actual** (`networking/httproutes/home/actual.yaml`):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: actual
  namespace: actual
spec:
  parentRefs:
    - name: envoy-gateway-home
      namespace: envoy-gateway-system
  hostnames:
    - "finance.home.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: desktop-proxy
          namespace: wol-proxy
          port: 8080
```

**vinyl-manager** (`networking/httproutes/home/vinyl-manager.yaml`) — simplify to single rule since proxy handles all paths:
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: vinyl-manager-home
  namespace: vinyl-manager
spec:
  parentRefs:
    - name: envoy-gateway-home
      namespace: envoy-gateway-system
  hostnames:
    - "vinyl.home.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: desktop-proxy
          namespace: wol-proxy
          port: 8080
```

Note on vinyl-manager routing: The existing split routing (`/api` → backend, `/` → frontend) is simplified here. The proxy maps `vinyl.home.timosur.com` to the frontend service. If the frontend doesn't internally proxy `/api` to the backend, this will need a follow-up `pathOverrides` feature in the proxy config. For now, assume the frontend handles API proxying.

- [ ] **Step 3: Update internet HTTPRoutes**

**paperless** (`networking/httproutes/internet/paperless.yaml`):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: paperless
  namespace: paperless
spec:
  parentRefs:
    - name: envoy-gateway-internet
      namespace: envoy-gateway-internet-system
      sectionName: https
  hostnames:
    - "docs.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: desktop-proxy
          namespace: wol-proxy
          port: 8080
```

**mealie** (`networking/httproutes/internet/mealie.yaml`):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mealie
  namespace: mealie
spec:
  parentRefs:
    - name: envoy-gateway-internet
      namespace: envoy-gateway-internet-system
      sectionName: https
  hostnames:
    - "mealie.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: desktop-proxy
          namespace: wol-proxy
          port: 8080
```

**n8n** (`networking/httproutes/internet/n8n.yaml`):
```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: n8n
  namespace: n8n
spec:
  parentRefs:
    - name: envoy-gateway-internet
      namespace: envoy-gateway-internet-system
      sectionName: https
  hostnames:
    - "automate.timosur.com"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: desktop-proxy
          namespace: wol-proxy
          port: 8080
```

- [ ] **Step 4: Register paperless in home kustomization**

Add `paperless.yaml` to `networking/httproutes/home/kustomization.yaml` (file exists but is not listed):

```yaml
resources:
  - actual.yaml
  - agents.yaml
  - agents-backend-policy.yaml
  - argocd.yaml
  - garden.yaml
  - grafana.yaml
  - home-assistant.yaml
  - paperless.yaml
  - pi-hole.yaml
  - vinyl-manager.yaml
```

- [ ] **Step 5: Commit**

```bash
git add networking/httproutes/ apps/wol-proxy/reference-grant.yaml apps/wol-proxy/kustomization.yaml
git commit -m "feat: route desktop app HTTPRoutes through wol-proxy"
```

---

### Task 18: Build and push updated wol-proxy image

**Files:**
- `services/wol-proxy/` (build context)

- [ ] **Step 1: Build and push**

```bash
cd services/wol-proxy
docker build -t ghcr.io/timosur/homelab/wol-proxy:latest .
docker push ghcr.io/timosur/homelab/wol-proxy:latest
```

- [ ] **Step 2: Restart wol-proxy deployment**

```bash
kubectl rollout restart deployment wol-proxy -n wol-proxy
kubectl rollout status deployment wol-proxy -n wol-proxy
kubectl logs -n wol-proxy -l app=wol-proxy --tail=20
```

Expected: Logs show both Ollama backend on :11434 and desktop node-group on :8080.

- [ ] **Step 3: Commit**

```bash
git add services/wol-proxy/
git commit -m "feat(wol-proxy): complete host-based routing for desktop node-group"
```
