terraform {
  cloud {
    organization = "timosur"

    workspaces {
      name = "homelab"
    }
  }
}