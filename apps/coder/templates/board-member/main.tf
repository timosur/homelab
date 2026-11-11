terraform {
  required_providers {
    coder = {
      source  = "coder/coder"
      version = "2.20.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.4.0"
    }
  }
}

provider "kubernetes" {}

data "coder_workspace" "me" {}

data "coder_workspace_owner" "me" {}

data "coder_external_auth" "github" {
  id = "github"
}

resource "coder_agent" "dev" {
  os   = "linux"
  arch = "amd64"
  dir  = "/home/coder"

  display_apps {
    ssh_helper = true
  }
}

module "dotfiles" {
  count    = data.coder_workspace.me.start_count
  source   = "registry.coder.com/coder/dotfiles/coder"
  version  = "1.2.0"
  agent_id = coder_agent.dev.id

  default_dotfiles_uri = "https://github.com/timosur/dotfiles.git"
  user                 = "coder"
}

resource "terraform_data" "power_preflight" {
  count = data.coder_workspace.me.start_count

  # Re-run on every start build, including retries after a partially completed build.
  triggers_replace = timestamp()

  provisioner "local-exec" {
    command = "sh \"${path.module}/preflight.sh\""
    environment = {
      WORKSPACE_ID = data.coder_workspace.me.id
    }
  }
}

resource "kubernetes_persistent_volume_claim_v1" "home" {
  depends_on = [terraform_data.power_preflight]

  metadata {
    name      = "coder-${data.coder_workspace.me.id}-home"
    namespace = "coder"
    labels = {
      "app.kubernetes.io/name"      = "coder-workspace"
      "app.kubernetes.io/component" = "development-workspace"
      "coder.workspace.id"          = data.coder_workspace.me.id
      "coder.workspace.owner"       = data.coder_workspace_owner.me.name
    }
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = "homelab-iscsi"

    resources {
      requests = {
        storage = "100Gi"
      }
    }
  }
}

resource "kubernetes_pod_v1" "workspace" {
  count = data.coder_workspace.me.start_count

  depends_on = [terraform_data.power_preflight]

  metadata {
    name      = "coder-${data.coder_workspace.me.id}"
    namespace = "coder"
    labels = {
      "app.kubernetes.io/name"      = "coder-workspace"
      "app.kubernetes.io/component" = "development-workspace"
      "coder.workspace.id"          = data.coder_workspace.me.id
      "coder.workspace.owner"       = data.coder_workspace_owner.me.name
    }
  }

  spec {
    automount_service_account_token = false
    security_context {
      run_as_user  = 1000
      run_as_group = 1000
      fs_group     = 1000
    }

    affinity {
      node_affinity {
        required_during_scheduling_ignored_during_execution {
          node_selector_term {
            match_expressions {
              key      = "kubernetes.io/hostname"
              operator = "In"
              values   = ["homelab-amd-desktop", "homelab-gpu"]
            }
          }
        }
      }
    }

    toleration {
      key      = "availability"
      operator = "Equal"
      value    = "daytime"
      effect   = "NoSchedule"
    }

    toleration {
      key      = "gpu"
      operator = "Equal"
      value    = "true"
      effect   = "NoSchedule"
    }

    container {
      name    = "dev"
      image   = "ghcr.io/timosur/homelab/coder-workspace:sha-fd710c66858d82b465d6c2973345dd248e4677c9@sha256:db6aa41f4886ebef94e6a7915c6299e83b4d6eb154c647c625ac524e3be3a102"
      command = ["sh", "-c", coder_agent.dev.init_script]

      env {
        name  = "CODER_AGENT_TOKEN"
        value = coder_agent.dev.token
      }

      volume_mount {
        name       = "home"
        mount_path = "/home/coder"
      }
    }

    volume {
      name = "home"

      persistent_volume_claim {
        claim_name = kubernetes_persistent_volume_claim_v1.home.metadata[0].name
      }
    }
  }
}
