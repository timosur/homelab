# GitOps Deployment Setup

This document explains how to set up the automated GitOps deployment pipeline for your homelab.

## Prerequisites

1. **Azure Subscription** with appropriate permissions
2. **AKS Cluster** or Kubernetes cluster accessible from GitHub Actions
3. **GitHub Repository** with Actions enabled

## GitHub Repository Configuration

### Required Secrets

Set these in your GitHub repository under **Settings > Secrets and variables > Actions > Repository secrets**:

```bash
# Azure authentication (for Workload Identity)
AZURE_CLIENT_ID=<your-github-actions-app-registration-client-id>
AZURE_TENANT_ID=<your-azure-tenant-id>
AZURE_SUBSCRIPTION_ID=<your-azure-subscription-id>

# Optional: If using existing Service Principal
AZURE_SP_CLIENT_SECRET=<your-existing-service-principal-secret>
```

### Required Variables

Set these in your GitHub repository under **Settings > Secrets and variables > Actions > Repository variables**:

```bash
# Azure resources
AZURE_RESOURCE_GROUP=<your-resource-group-name>
AZURE_KEYVAULT_NAME=<your-keyvault-name>

# Kubernetes cluster
CLUSTER_NAME=<your-aks-cluster-name>
CLUSTER_RESOURCE_GROUP=<your-cluster-resource-group>
```

## Azure Setup

### 1. Create Azure Key Vault

```bash
# Set your variables
RESOURCE_GROUP="homelab-rg"
KEYVAULT_NAME="homelab-kv-$(openssl rand -hex 4)"
LOCATION="germanywestcentral"

# Create resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# Create Key Vault
az keyvault create \
  --name $KEYVAULT_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

### 2. Set up GitHub Actions Workload Identity (Recommended)

```bash
# Create App Registration for GitHub Actions
APP_NAME="github-actions-homelab"
CLIENT_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)

# Create Service Principal
az ad sp create --id $CLIENT_ID

# Create federated credential for GitHub Actions
REPO_URL="https://github.com/timosur/homelab"
az ad app federated-credential create \
  --id $CLIENT_ID \
  --parameters "{
    \"name\": \"github-actions\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:timosur/homelab:ref:refs/heads/main\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"

# Assign permissions
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
az role assignment create \
  --assignee $CLIENT_ID \
  --role "Key Vault Administrator" \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.KeyVault/vaults/$KEYVAULT_NAME"

az role assignment create \
  --assignee $CLIENT_ID \
  --role "Azure Kubernetes Service Cluster User Role" \
  --scope "/subscriptions/$SUBSCRIPTION_ID"

echo "GitHub Actions Client ID: $CLIENT_ID"
echo "Add this to your GitHub repository secrets as AZURE_CLIENT_ID"
```

## What the Pipeline Does

1. **Authenticates to Azure** using Workload Identity
2. **Connects to your AKS cluster**
3. **Creates External Secrets namespace** if it doesn't exist
4. **Creates/updates Azure Service Principal** for External Secrets Operator
5. **Creates the azure-secret** Kubernetes secret with SP credentials
6. **Updates the ClusterSecretStore** with your actual Key Vault URL
7. **Creates PostgreSQL secrets** in Azure Key Vault (if they don't exist)
8. **Verifies External Secrets** are working

## Manual Trigger

You can manually trigger the deployment:

1. Go to your GitHub repository
2. Click **Actions** tab
3. Select **Deploy GitOps** workflow
4. Click **Run workflow**

## Troubleshooting

### Check External Secrets Status

```bash
kubectl get externalsecrets -A
kubectl describe externalsecret mealie-postgres-credentials -n mealie
```

### Check ClusterSecretStore

```bash
kubectl get clustersecretstore
kubectl describe clustersecretstore azure-keyvault-store
```

### Check Secrets

```bash
kubectl get secrets -n mealie
kubectl get secrets -n external-secrets-system
```

## Security Notes

- The pipeline uses Azure Workload Identity (OIDC) for secure authentication
- Service Principal secrets are created automatically and stored securely
- PostgreSQL passwords are generated randomly and stored only in Azure Key Vault
- No sensitive data is stored in Git or GitHub repository settings (except for the Azure authentication setup)
