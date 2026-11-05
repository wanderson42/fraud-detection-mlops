#!/usr/bin/env bash
# Install the pinned Linux amd64 CLI only; no infrastructure operations.
set -euo pipefail
version=1.17.1
expected=4638628058e71a55212b1c563712eac03b902128f40740f3dc8174df12135061
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Linux amd64 required'; exit 2; }
if command -v terraform >/dev/null 2>&1; then
  if terraform version -json | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin)["terraform_version"] != "1.17.1")'; then
    exit 0
  fi
  echo 'Existing CLI preserved; installing the pinned CLI in ~/.local/bin.'
fi
umask 077
task_tmp=$(mktemp -d)
trap 'rm -rf -- "$task_tmp"' EXIT
archive="terraform_${version}_linux_amd64.zip"
curl --fail --location --proto '=https' --tlsv1.2 \
  "https://releases.hashicorp.com/terraform/${version}/${archive}" -o "$task_tmp/$archive"
printf '%s  %s\n' "$expected" "$task_tmp/$archive" | sha256sum --check --status
unzip -q "$task_tmp/$archive" terraform -d "$task_tmp"
mkdir -p "$HOME/.local/bin"
test ! -e "$HOME/.local/bin/terraform" || { echo "Existing ~/.local/bin/terraform preserved; inspect version and PATH"; exit 2; }
install -m 0755 "$task_tmp/terraform" "$HOME/.local/bin/terraform"
echo 'Terraform 1.17.1 installed in ~/.local/bin; ensure this directory is on PATH.'
