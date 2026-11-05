#!/usr/bin/env bash
# Provider validation only: no kubeconfig, cluster, state or cloud account needed.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
terraform version -json | python3 -c 'import json,sys; assert json.load(sys.stdin)["terraform_version"] == "1.17.1"'
terraform -chdir="$repo_root/infra/terraform/local-foundation" fmt -check
umask 077
task_tmp=$(mktemp -d)
trap 'rm -rf -- "$task_tmp"' EXIT
cp "$repo_root"/infra/terraform/local-foundation/*.tf "$task_tmp/"
cp "$repo_root/infra/terraform/local-foundation/.terraform.lock.hcl" "$task_tmp/"
cp -R "$repo_root/infra/helm/fraud-platform" "$task_tmp/chart"
# Discard inherited CLI argument injection and Terraform debug output.
unset TF_CLI_ARGS TF_CLI_ARGS_init TF_CLI_ARGS_validate TF_LOG TF_LOG_PATH TF_DATA_DIR TF_WORKSPACE
export TF_IN_AUTOMATION=1 CHECKPOINT_DISABLE=1
terraform -chdir="$task_tmp" init -backend=false -input=false -lockfile=readonly -no-color
terraform -chdir="$task_tmp" validate -no-color
