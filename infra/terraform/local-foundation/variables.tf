variable "kubeconfig" {
  type = string
  validation {
    condition     = startswith(var.kubeconfig, "/")
    error_message = "An explicit absolute private kubeconfig is required."
  }
}
variable "owner_id" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{32}$", var.owner_id))
    error_message = "Use the initialized owner UUID."
  }
}
variable "credentials" {
  type      = map(string)
  sensitive = true
  validation {
    condition = toset(keys(var.credentials)) == toset([
      "postgres-password", "rustfs-access-key", "rustfs-secret-key"
    ]) && alltrue([for v in values(var.credentials) : can(regex("^[a-f0-9]{24,64}$", v))])
    error_message = "Use the three private generated credentials."
  }
}
