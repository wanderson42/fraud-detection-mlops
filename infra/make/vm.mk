# Client accesses need only Python 3.10+ / OpenSSH on Linux, not the scientific venv.
VM_CLIENT_PYTHON ?= python3
VM_PROFILE ?= default
VM_HOST ?=
VM_USER ?=
VM_SSH_PORT ?=
VM_ROOT ?=
VM_SERVICES ?= portal,grafana,mlflow
VM_LOCAL_PORTS ?=
VM_REMOTE_PORTS ?=
export VM_PROFILE VM_HOST VM_USER VM_SSH_PORT VM_ROOT VM_SERVICES VM_LOCAL_PORTS VM_REMOTE_PORTS
.PHONY: vm-access-up vm-access-status vm-access-stop vm-setup-check

## Open owned SSH tunnels; free client ports by default; configure VM_HOST/VM_ROOT/PORTAL_ID
vm-access-up:
	$(VM_CLIENT_PYTHON) -m scripts.vm_access.entry up --portal-id "$(PORTAL_ID)"

## Show actual local URLs and HTTP readiness for this checkout/profile
vm-access-status:
	$(VM_CLIENT_PYTHON) -m scripts.vm_access.entry status

## Close only this client's recorded SSH tunnels; preserve VM services and workloads
vm-access-stop:
	$(VM_CLIENT_PYTHON) -m scripts.vm_access.entry stop

## Read tool versions, host budget and inotify; no package installation or cluster changes
vm-setup-check:
	bash scripts/vm_setup.sh check

# New, powered-off host VM only. No OS installation, adoption or deletion.
VM_NEW_NAME ?= fraud-bootstrap-v1
VM_NEW_BASE ?= $(HOME)/VirtualBox VMs
VM_ISO ?=
VM_ISO_SHA256 ?=
VM_NEW_CPUS ?= 6
VM_NEW_MEMORY_MIB ?= 8192
VM_NEW_DISK_GIB ?= 80
VM_NEW_SSH_PORT ?= 0
VM_NEW_ARGS = --name "$(VM_NEW_NAME)" --base "$(VM_NEW_BASE)" --iso "$(VM_ISO)" --iso-sha256 "$(VM_ISO_SHA256)" --cpus "$(VM_NEW_CPUS)" --memory-mib "$(VM_NEW_MEMORY_MIB)" --disk-gib "$(VM_NEW_DISK_GIB)" --ssh-port "$(VM_NEW_SSH_PORT)"
.PHONY: vm-create-plan vm-create
## Preview a NEW powered-off VirtualBox VM; verify ISO checksum and conflicts; host only
vm-create-plan:
	$(VM_CLIENT_PYTHON) -m scripts.vm_provision.controller plan $(VM_NEW_ARGS)

## Create a NEW powered-off VM; no boot, installation, deletion or existing VM modification
vm-create:
	$(VM_CLIENT_PYTHON) -m scripts.vm_provision.controller create $(VM_NEW_ARGS)
