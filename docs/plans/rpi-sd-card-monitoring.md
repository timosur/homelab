# Raspberry Pi SD Card Health Monitoring

## Goal

Add SD card health alerting and a Grafana dashboard for the two ARM Raspberry Pi worker nodes (`homelab-arm-small`, `homelab-arm-large`). Uses existing node-exporter metrics — no new exporters needed. Implemented as a PrometheusRule CRD + Grafana Dashboard ConfigMap added to the existing `apps/monitoring/` Kustomize overlay.

## Context

- **Monitoring stack**: kube-prometheus-stack v82.8.0 deployed via ArgoCD multi-source (Helm + Kustomize overlay)
- **Node-exporter**: Already running as DaemonSet on all nodes (including ARM)
- **Alertmanager**: Already configured with email alerts to `homelab@timosur.com` via Gmail SMTP
- **Kustomize overlay**: `apps/monitoring/` currently has namespace.yaml, external-secret.yaml, alloy-ingress-policy.yaml
- **ARM nodes**: `homelab-arm-small` and `homelab-arm-large` — both Raspberry Pis with SD cards
- **SD card device**: Typically `/dev/mmcblk0` on RPi; root filesystem mounted at `/`
- **No SMART support**: SD cards don't expose S.M.A.R.T. data, so we rely on filesystem + I/O metrics from node-exporter

## Steps

### Phase 1: PrometheusRule CRD — SD Card Alerts

Create `apps/monitoring/sd-card-prometheusrule.yaml`

A `PrometheusRule` CRD with these alert rules, all scoped to ARM nodes via `node=~"homelab-arm-.*"`:

1. **`SDCardHighDiskUsage`** (warning) — root filesystem usage > 80%
   - PromQL: `(1 - node_filesystem_avail_bytes{mountpoint="/", fstype!="tmpfs", node=~"homelab-arm-.*"} / node_filesystem_size_bytes{...}) * 100 > 80`
   - Labels: `severity: warning`

2. **`SDCardCriticalDiskUsage`** (critical) — root filesystem usage > 90%
   - Same as above with threshold 90
   - Labels: `severity: critical`

3. **`SDCardReadOnly`** (critical) — root filesystem becomes read-only (sign of SD card failure)
   - PromQL: `node_filesystem_readonly{mountpoint="/", node=~"homelab-arm-.*"} == 1`
   - Labels: `severity: critical`

4. **`SDCardPredictedFull`** (warning) — `predict_linear` predicts root filesystem full within 24h
   - PromQL: `predict_linear(node_filesystem_avail_bytes{mountpoint="/", node=~"homelab-arm-.*"}[6h], 24*3600) < 0`
   - Labels: `severity: warning`

5. **`SDCardHighWriteRate`** (warning) — sustained high write rate wearing out SD card (> 10 MB/s for 15min)
   - PromQL: `rate(node_disk_written_bytes_total{device=~"mmcblk.*", node=~"homelab-arm-.*"}[5m]) > 10 * 1024 * 1024` with `for: 15m`
   - Labels: `severity: warning`
   - Note: Threshold adjustable after observing baseline

### Phase 2: Grafana Dashboard ConfigMap

Create `apps/monitoring/sd-card-dashboard-configmap.yaml`

A ConfigMap with label `grafana_dashboard: "1"` containing a Grafana dashboard JSON. The Grafana sidecar (enabled by default in kube-prometheus-stack) auto-discovers ConfigMaps with this label.

Dashboard panels (scoped to ARM nodes):

1. **Disk Usage %** — Gauge per node showing current root filesystem usage
2. **Available Space Over Time** — Time series of `node_filesystem_avail_bytes` for root FS
3. **Predicted Time Until Full** — Stat panel using `predict_linear`
4. **Disk Write Rate** — Time series of `rate(node_disk_written_bytes_total)` for mmcblk devices
5. **Disk Read Rate** — Time series of `rate(node_disk_read_bytes_total)` for mmcblk devices
6. **Total Bytes Written** — Counter showing cumulative writes (lifetime wear indicator)
7. **I/O Utilization %** — `rate(node_disk_io_time_seconds_total)` showing how busy the disk is
8. **Filesystem Read-Only Status** — Stat panel (green=ok, red=readonly)

Dashboard will have a variable for node selection (defaulting to both ARM nodes).

### Phase 3: Kustomize Integration

Update `apps/monitoring/kustomization.yaml` — add both new resource files to `resources:`.

After git push, ArgoCD auto-syncs:
- Prometheus Operator picks up the PrometheusRule → alerts become active
- Grafana sidecar picks up the dashboard ConfigMap → dashboard appears in Grafana

No changes needed to `apps/_argocd/monitoring-app.yaml` (multi-source already includes `apps/monitoring/`).

## Files Changed

| File | Action |
|---|---|
| `apps/monitoring/sd-card-prometheusrule.yaml` | **Create** — PrometheusRule CRD |
| `apps/monitoring/sd-card-dashboard-configmap.yaml` | **Create** — Grafana Dashboard ConfigMap |
| `apps/monitoring/kustomization.yaml` | **Edit** — add both new resources |

## Verification

1. **Syntax**: Validate YAML is well-formed and PrometheusRule CRD has correct apiVersion (`monitoring.coreos.com/v1`)
2. **ArgoCD sync**: After push, verify the monitoring Application syncs successfully
3. **Prometheus**: In Grafana Explore, query `node_filesystem_avail_bytes{node=~"homelab-arm-.*"}` to confirm metrics exist and `node` label matches
4. **Alerts**: Check Alertmanager UI → verify SD card rules appear under "Inactive" (assuming healthy state)
5. **Dashboard**: Navigate to Grafana → Dashboards → search "SD Card" → verify panels render with data
6. **Node label verification**: If `node=~"homelab-arm-.*"` doesn't match, check actual label values via `up{job="node-exporter"}` and adjust the regex in both the PrometheusRule and dashboard

## Decisions

- **Node targeting**: Using `node=~"homelab-arm-.*"` regex to match both ARM nodes. If node-exporter uses IP-based instance labels instead of hostnames, the regex will need adjustment (verification step 6).
- **SD card device**: Using `device=~"mmcblk.*"` for I/O metrics (standard RPi SD card device name). Root filesystem alerts use `mountpoint="/"` which is device-agnostic.
- **Thresholds**: 80% warning / 90% critical for disk usage; 10 MB/s sustained write rate as warning. Write rate threshold is a starting point to tune after observing baseline.
- **Scope**: Only ARM nodes. The AMD control plane node is excluded since it uses an SSD/HDD.
- **No new exporters**: Everything uses existing node-exporter metrics. No smartmon-exporter since SD cards don't support S.M.A.R.T.

## Notes

- **Write rate threshold**: 10 MB/s sustained for 15min is a reasonable starting point. After deployment, check the dashboard to see normal write patterns and adjust. K3s logging or etcd WAL writes can be bursty — may need to increase the threshold or `for` duration.
- **Node label format**: If kube-prometheus-stack's node-exporter doesn't set a `node` label matching the Kubernetes node name, we may need to use `instance` label or join with `kube_node_info`. Easy to adjust after checking verification step 6.
