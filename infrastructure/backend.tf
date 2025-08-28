terraform {
  cloud {
    organization = "timosur"
    
    workspaces {
      name = "homelab"
    }
  }
  
  # Force local execution even with Terraform Cloud
  # This way we get remote state storage but local execution
}