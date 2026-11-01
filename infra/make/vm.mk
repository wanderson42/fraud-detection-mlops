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
