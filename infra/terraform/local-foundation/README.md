# Isolated local foundation

Use `make iac-init`, `iac-validate`, `iac-preflight`, `iac-plan`, then apply the
reviewed plan by ID and SHA256. Do not apply from this versioned directory:
the controller copies the chart/configuration and private inputs to a guarded
workspace. The live test is opt-in and creates/restarts only own resources.

Terraform 1.17.1; Kubernetes provider 3.4.0; Helm provider 3.3.0.
Provider lock is committed. No AWS credentials or cloud resources are required.

[Procedure, private state, ownership and acceptance limits](../../../docs/operations/LOCAL_IAC.md).
