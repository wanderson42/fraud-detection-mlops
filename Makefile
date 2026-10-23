SHELL := /bin/bash
.DEFAULT_GOAL := help
POETRY ?= poetry
PROJECT_NAME = fraud-detection-mlops
PYTHON_VERSION = 3.14
PYTHON_INTERPRETER = python

# platform.mk also serializes explicit operational goals when Make receives -j.
include infra/make/platform.mk
include infra/make/round.mk
include infra/make/updates.mk
include infra/make/product.mk
include infra/make/serving-recovery.mk
include infra/make/case-explanations.mk
include infra/make/portal.mk
include infra/make/access.mk
include infra/make/development.mk
include infra/make/science.mk

.PHONY: help help-all
## Show the daily entrypoints; help-all lists provisioning, science and failure rehearsals
help:
	@printf '%s\n' \
	  'Operação:    platform-status | platform-host-check' \
	  'Acessos:     platform-api | platform-mlflow | platform-grafana | portal-open' \
	  'Verificação: platform-check | portal-verify | portal-performance-verify | product-verify' \
	  'Parar/subir: platform-stop | platform-start (requerem baseline salvo e declaração de writers)' \
	  'Desenvolver: validate | portal-ui-test' \
	  'Background:  access-up | access-status | access-logs | access-stop-all' \
	  'Catálogo:    make help-all' \
	  'Guia:       docs/operations/PRODUCT_OPERATIONS.md'

## List every documented target without running Poetry, Docker or Kubernetes
help-all:
	@awk '\
	/^## / { description = substr($$0, 4); next } \
	/^[A-Za-z0-9_-]+:/ { \
	  name = $$0; sub(/:.*/, "", name); \
	  if ($$0 ~ /:.*## /) { description = $$0; sub(/.*## /, "", description) } \
	  if (description != "") printf "%-32s %s\n", name, description; \
	  description = "" \
	}' $(MAKEFILE_LIST)
