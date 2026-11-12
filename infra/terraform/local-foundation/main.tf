locals {
  namespace = "fraud-iac-foundation-v1"
  release   = "fraud-iac-foundation"
  secret    = "fraud-iac-foundation-credentials"
}
resource "kubernetes_namespace_v1" "foundation" {
  metadata {
    name = local.namespace
    annotations = {
      "fraud.iac/owner-id" = var.owner_id
    }
    labels = {
      "app.kubernetes.io/managed-by" = "Terraform"
    }
  }
  lifecycle {
    prevent_destroy = true
  }
}
resource "kubernetes_secret_v1" "credentials" {
  metadata {
    name      = local.secret
    namespace = kubernetes_namespace_v1.foundation.metadata[0].name
    annotations = {
      "fraud.iac/owner-id" = var.owner_id
    }
  }
  type = "Opaque"
  data = var.credentials
  lifecycle {
    prevent_destroy = true
  }
}
resource "helm_release" "foundation" {
  name             = local.release
  namespace        = kubernetes_namespace_v1.foundation.metadata[0].name
  chart            = "${abspath(path.module)}/chart"
  version          = "0.1.0"
  description      = "fraud-iac:${var.owner_id}"
  create_namespace = false
  wait             = true
  timeout          = 600
  atomic           = false
  cleanup_on_fail  = false
  values = [yamlencode({
    existingSecret = kubernetes_secret_v1.credentials.metadata[0].name
  })]
  lifecycle {
    prevent_destroy = true
  }
}
