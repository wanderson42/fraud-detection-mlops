terraform {
  required_version = "= 1.17.1"
  backend "local" {}
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "= 3.4.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "= 3.3.0"
    }
  }
}
provider "kubernetes" {
  config_path    = var.kubeconfig
  config_context = "kind-fraud-platform"
}
provider "helm" {
  kubernetes = {
    config_path    = var.kubeconfig
    config_context = "kind-fraud-platform"
  }
}
