IAC_SESSION_ID ?= clean-install-arrival-v1
IAC_PLAN_ID ?=
IAC_PLAN_SHA256 ?=
IAC_NO_OTHER_WRITERS ?= 0
.PHONY: iac-init iac-validate iac-preflight iac-plan iac-apply iac-verify iac-rehearse
## Initialize private IaC workspace for the new foundation; no resources created
iac-init:
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller init --session-id "$(IAC_SESSION_ID)"
## Initialize locked providers and validate Terraform in the private workspace
iac-validate:
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller validate
## Check cluster ownership, accepted session and available host capacity
iac-preflight:
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller preflight
## Produce a saved private plan and sanitized actions/digest for review; no apply
iac-plan:
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller plan
## Apply the exact reviewed plan; requires ID, digest and no other writers declaration
iac-apply:
	@test "$(IAC_NO_OTHER_WRITERS)" = 1 || { echo 'Set IAC_NO_OTHER_WRITERS=1'; exit 2; }
	@test -n "$(IAC_PLAN_ID)" -a -n "$(IAC_PLAN_SHA256)" || { echo 'Set plan ID and SHA256'; exit 2; }
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller apply --plan-id "$(IAC_PLAN_ID)" --expected-plan-sha256 "$(IAC_PLAN_SHA256)" --no-other-writers
## Read existing own markers and verify no-change plan; no pod recreation
iac-verify:
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller verify
## Explicit own SQL/S3 writes and own pod recreation; preserve accepted session
iac-rehearse:
	@test "$(IAC_NO_OTHER_WRITERS)" = 1 || { echo 'Set IAC_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.platform.terraform_foundation.controller rehearse --no-other-writers
