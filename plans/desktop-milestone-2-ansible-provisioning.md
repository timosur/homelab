# Milestone 2: Ansible — Provision Desktop Node

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `homelab-amd-desktop` (192.168.2.241) as a K3s worker node with `availability=daytime:NoSchedule` taint, managed via Ansible.

**Architecture:** Create an Ansible role (`k3s-desktop-worker`) based on the existing `k3s-gpu-worker` pattern but with daytime-specific labels and taint. The node joins the K3s cluster with labels `availability=daytime`, `node.kubernetes.io/gpu=amd`, `gpu-type=vulkan` and taint `availability=daytime:NoSchedule`. Only pods with explicit tolerations will schedule on this node.

**Tech Stack:** Ansible, K3s, SSH

**Dependencies:** None — this milestone is independent of Milestone 1. However, the desktop node must be accessible via SSH before Tasks 10-11 can run.

**Parallelism:** Tasks 7-9 (inventory + role + playbook) can run in parallel. Task 10 (SSH setup) is a manual prerequisite for Task 11 (provisioning run).

---

## Node Details

| Property | Value                                                                   |
| -------- | ----------------------------------------------------------------------- |
| Hostname | `homelab-amd-desktop`                                                   |
| IP       | `192.168.2.241`                                                         |
| WoL MAC  | `30:9c:23:8a:30:e3`                                                     |
| SSH User | `timosur`                                                               |
| Labels   | `availability=daytime`, `node.kubernetes.io/gpu=amd`, `gpu-type=vulkan` |
| Taint    | `availability=daytime:NoSchedule`                                       |

---

## Files

### New files
- `ansible/roles/k3s-desktop-worker/tasks/main.yml` — K3s agent join with desktop labels/taint

### Modified files
- `ansible/inventory.yml` — Add `homelab-amd-desktop` to new `desktop_workers` group
- `ansible/playbooks/k3s-cluster.yml` — Add desktop workers play

---

### Task 7: Add desktop node to inventory

**Files:**
- Modify: `ansible/inventory.yml`

- [ ] **Step 1: Add `desktop_workers` group**

```yaml
---
all:
  vars:
    ansible_user: timosur
    k3s_version: v1.34.2+k3s1
    cilium_cli_version: v0.18.9

  children:
    k3s_cluster:
      children:
        control_plane:
          hosts:
            homelab-amd:
              ansible_connection: local
              ansible_host: localhost
              node_ip: "{{ lookup('pipe', 'hostname -I | awk \"{print \\$1}\"') }}"

        workers:
          hosts:
            homelab-arm-small:
              ansible_host: homelab-arm-small
              node_ip: "{{ lookup('pipe', 'ssh homelab-arm-small hostname -I | awk \"{print \\$1}\"') }}"
              extra_disabled_services:
                - docker.service
                - dphys-swapfile.service

            homelab-arm-large:
              ansible_host: homelab-arm-large
              node_ip: "{{ lookup('pipe', 'ssh homelab-arm-large hostname -I | awk \"{print \\$1}\"') }}"
              extra_disabled_services:
                - docker.service
                - dphys-swapfile.service

        gpu_workers:
          hosts:
            homelab-gpu:
              ansible_host: 192.168.2.47
              node_ip: "{{ lookup('pipe', 'ssh homelab-gpu hostname -I | awk \"{print \\$1}\"') }}"
              wol_mac: "2c:f0:5d:05:9d:80"

        desktop_workers:
          hosts:
            homelab-amd-desktop:
              ansible_host: 192.168.2.241
              node_ip: "{{ lookup('pipe', 'ssh homelab-amd-desktop hostname -I | awk \"{print \\$1}\"') }}"
              wol_mac: "30:9c:23:8a:30:e3"
```

- [ ] **Step 2: Commit**

```bash
git add ansible/inventory.yml
git commit -m "feat: add homelab-amd-desktop to inventory"
```

---

### Task 8: Create k3s-desktop-worker Ansible role

Based on `k3s-gpu-worker` but with daytime-specific labels/taint instead of GPU taint.

**Files:**
- Create: `ansible/roles/k3s-desktop-worker/tasks/main.yml`

- [ ] **Step 1: Create the role**

```yaml
---
- name: Install open-iscsi for Synology CSI iSCSI support
  ansible.builtin.package:
    name: open-iscsi
    state: present
  become: true

- name: Enable and start iscsid service
  ansible.builtin.systemd:
    name: iscsid
    state: started
    enabled: true
  become: true

- name: Check if k3s is already installed
  ansible.builtin.stat:
    path: /usr/local/bin/k3s
  register: k3s_binary

- name: Get control plane IP
  ansible.builtin.set_fact:
    control_plane_ip: "{{ hostvars['homelab-amd']['node_ip'] }}"

- name: Get k3s token from control plane hostvars
  ansible.builtin.set_fact:
    k3s_token: "{{ hostvars['homelab-amd']['k3s_token'] }}"
  when: hostvars['homelab-amd']['k3s_token'] is defined

- name: Read k3s token directly from control plane (when run with --limit)
  ansible.builtin.slurp:
    src: /var/lib/rancher/k3s/server/node-token
  register: k3s_token_raw
  delegate_to: homelab-amd
  become: true
  when: k3s_token is not defined

- name: Set k3s token from direct read
  ansible.builtin.set_fact:
    k3s_token: "{{ k3s_token_raw.content | b64decode | trim }}"
  when: k3s_token is not defined and k3s_token_raw is not skipped

- name: Verify token is available
  ansible.builtin.fail:
    msg: "K3S token not available from control plane. Ensure control plane role has run successfully."
  when: k3s_token is not defined or k3s_token | length == 0

- name: Debug connection information
  ansible.builtin.debug:
    msg:
      - "Control plane IP: {{ control_plane_ip }}"
      - "Worker node IP: {{ node_ip }}"
      - "K3S Token (first 20 chars): {{ k3s_token[:20] }}..."

- name: Create k3s config directory
  ansible.builtin.file:
    path: /etc/rancher/k3s
    state: directory
    mode: "0755"
  become: true

- name: Create k3s agent config with desktop labels and taints
  ansible.builtin.copy:
    dest: /etc/rancher/k3s/config.yaml
    content: |
      node-label:
        - "availability=daytime"
        - "node.kubernetes.io/gpu=amd"
        - "gpu-type=vulkan"
      node-taint:
        - "availability=daytime:NoSchedule"
    mode: "0644"
  become: true

- name: Install k3s agent
  ansible.builtin.shell: |
    set -o pipefail
    curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="{{ k3s_version }}" \
      K3S_URL="https://{{ control_plane_ip }}:6443" \
      K3S_TOKEN="{{ k3s_token }}" \
      INSTALL_K3S_EXEC="--node-ip={{ node_ip }}" \
      sh -
  args:
    executable: /bin/bash
    creates: /usr/local/bin/k3s
  become: true
  when: not k3s_binary.stat.exists

- name: Ensure k3s-agent service is running
  ansible.builtin.systemd:
    name: k3s-agent
    state: started
    enabled: true
  become: true

- name: Wait for node to register
  ansible.builtin.pause:
    seconds: 30

- name: Check k3s-agent service status
  ansible.builtin.command: systemctl status k3s-agent --no-pager
  register: k3s_agent_status
  become: true
  changed_when: false
  failed_when: false

- name: Show k3s-agent status
  ansible.builtin.debug:
    var: k3s_agent_status.stdout_lines
```

- [ ] **Step 2: Commit**

```bash
git add ansible/roles/k3s-desktop-worker/tasks/main.yml
git commit -m "feat: add k3s-desktop-worker role with daytime labels/taint"
```

---

### Task 9: Add desktop workers play to k3s-cluster.yml

**Files:**
- Modify: `ansible/playbooks/k3s-cluster.yml`

- [ ] **Step 1: Add desktop workers play after GPU workers, before Cilium**

Add this play between "Setup GPU workers" and "Install and configure Cilium":

```yaml
- name: Setup desktop workers
  hosts: desktop_workers
  gather_facts: true
  become: true
  roles:
    - node-hardening
    - amd-gpu
    - k3s-desktop-worker
```

- [ ] **Step 2: Commit**

```bash
git add ansible/playbooks/k3s-cluster.yml
git commit -m "feat: add desktop workers play to k3s-cluster.yml"
```

---

### Task 10: Setup SSH key auth on desktop node

Before Ansible can manage the node, SSH key auth must be configured (currently password-only).

- [ ] **Step 1: Copy SSH public key to desktop node**

Run from local machine:

```bash
ssh-copy-id -i /Users/timosur/code/homelab/keys/id_ed25519.pub timosur@192.168.2.241
```

- [ ] **Step 2: Verify passwordless SSH works**

```bash
ssh -i /Users/timosur/code/homelab/keys/id_ed25519 timosur@homelab-amd-desktop "hostname"
```

Expected: `homelab-amd-desktop` without password prompt.

- [ ] **Step 3: Verify Ansible connectivity**

```bash
cd ansible
ansible homelab-amd-desktop -i inventory.yml -m ping
```

Expected: `SUCCESS`

---

### Task 11: Run Ansible to provision desktop node

- [ ] **Step 1: Run the playbook for desktop workers only**

```bash
cd ansible
ansible-playbook -i inventory.yml playbooks/k3s-cluster.yml --limit desktop_workers
```

- [ ] **Step 2: Verify node joined cluster**

```bash
kubectl get nodes -o wide
```

Expected: `homelab-amd-desktop` with status `Ready`, labels `availability=daytime`, taint `availability=daytime:NoSchedule`.

```bash
kubectl describe node homelab-amd-desktop | grep -A5 'Labels\|Taints'
```
