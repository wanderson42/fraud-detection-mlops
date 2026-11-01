#!/usr/bin/env bash
# Explicit phases for a NEW Ubuntu 24.04 amd64 VM. Never create/reset a cluster.
set -euo pipefail
phase="${1:-check}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

if [[ "$phase" == check ]]; then
  uname -sm
  free -h
  df -h "$repo_root"
  nproc
  for tool in git curl docker python3 poetry uv kind kubectl helm; do
    command -v "$tool" || true
  done
  if command -v docker >/dev/null; then
    docker info --format 'Engine={{.ServerVersion}} Driver={{.Driver}}'
    docker compose version
    docker buildx version
  fi
  if command -v poetry >/dev/null; then poetry --version; fi
  if command -v kind >/dev/null; then kind version; fi
  if command -v kubectl >/dev/null; then kubectl version --client; fi
  if command -v helm >/dev/null; then helm version --short; fi
  sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
  exit
fi

source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == 24.04 && "$(uname -m)" == x86_64 ]] || {
  echo 'Installer phases support Ubuntu 24.04 amd64 only; check is read-only.' >&2
  exit 2
}
[[ "$EUID" != 0 ]] || { echo 'Run as the project user; individual commands use sudo.' >&2; exit 2; }

case "$phase" in
  packages)
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl git make openssh-client pipx unzip
    ;;
  docker)
    if command -v docker >/dev/null; then
      echo 'Docker already installed; inspect check output instead of replacing it.' >&2
      exit 2
    fi
    sudo install -m 0755 -d /etc/apt/keyrings
    curl --fail --show-error --location https://download.docker.com/linux/ubuntu/gpg |
      sudo tee /etc/apt/keyrings/docker.asc >/dev/null
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    printf '%s\n' 'Types: deb' 'URIs: https://download.docker.com/linux/ubuntu' \
      'Suites: noble' 'Components: stable' 'Architectures: amd64' \
      'Signed-By: /etc/apt/keyrings/docker.asc' |
      sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
    sudo apt-get update
    engine_version='5:29.1.3-1~ubuntu.24.04~noble'
    sudo apt-get install -y "docker-ce=$engine_version" "docker-ce-cli=$engine_version" \
      containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER"
    echo 'Reconnect SSH before docker info. Docker group grants host administration.'
    ;;
  python)
    command -v uv >/dev/null || pipx install uv
    command -v poetry >/dev/null || pipx install 'poetry==2.4.3'
    [[ "$(poetry --version)" == 'Poetry (version 2.4.3)' ]] || {
      echo 'Use Poetry 2.4.3; existing installation was preserved.' >&2; exit 2;
    }
    uv python install 3.14.4
    cd "$repo_root"
    poetry config virtualenvs.in-project true --local
    poetry env use "$(uv python find 3.14.4)"
    poetry check --lock
    poetry sync --only main,pipeline,dev
    poetry run python --version
    ;;
  tools)
    stage="$(mktemp -d)"
    trap 'rm -rf -- "$stage"' EXIT
    cd "$stage"
    kind_url='https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64'
    curl -fsSL "$kind_url" -o kind-linux-amd64
    curl -fsSL "$kind_url.sha256sum" -o kind.sha256sum
    sha256sum -c kind.sha256sum
    kubectl_url='https://dl.k8s.io/release/v1.35.9/bin/linux/amd64/kubectl'
    curl -fsSL "$kubectl_url" -o kubectl
    curl -fsSL "$kubectl_url.sha256" -o kubectl.sha256
    printf '%s  kubectl\n' "$(cat kubectl.sha256)" | sha256sum -c -
    helm_file='helm-v4.3.1-linux-amd64.tar.gz'
    curl -fsSL "https://get.helm.sh/$helm_file" -o "$helm_file"
    curl -fsSL "https://get.helm.sh/$helm_file.sha256sum" -o helm.sha256sum
    sha256sum -c helm.sha256sum
    tar -xzf "$helm_file" linux-amd64/helm
    # Preserve preinstalled tools; do not silently upgrade or downgrade another project.
    for tool in kind kubectl helm; do
      if command -v "$tool" >/dev/null; then
        echo "Existing $tool preserved; inspect its version with check."
      else
        case "$tool" in kind) binary=kind-linux-amd64;; kubectl) binary=kubectl;; helm) binary=linux-amd64/helm;; esac
        sudo install -m 0755 "$binary" "/usr/local/bin/$tool"
      fi
    done
    ;;
  inotify)
    # Raise minimums without lowering a host's existing values.
    instances="$(sysctl -n fs.inotify.max_user_instances)"
    watches="$(sysctl -n fs.inotify.max_user_watches)"
    (( instances >= 512 )) || instances=512
    (( watches >= 524288 )) || watches=524288
    printf 'fs.inotify.max_user_instances=%s\nfs.inotify.max_user_watches=%s\n' "$instances" "$watches" |
      sudo tee /etc/sysctl.d/90-fraud-kind.conf >/dev/null
    sudo sysctl -p /etc/sysctl.d/90-fraud-kind.conf
    ;;
  *) echo 'Usage: bash scripts/vm_setup.sh check|packages|docker|python|tools|inotify' >&2; exit 2;;
esac
