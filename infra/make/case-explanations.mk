# Read-only investigation from the accepted product ledger; never a training step.
PRODUCT_EXPLANATION_ID ?= champion-shap-v1
PRODUCT_EXPLANATION_SHA256 ?=
.PHONY: product-explain product-explanations-verify product-explanations

## Explain bounded persisted cases using the exact champion weights; requires PRODUCT_NO_OTHER_WRITERS=1
product-explain:
	@test "$(PRODUCT_NO_OTHER_WRITERS)" = 1 || { echo 'Set PRODUCT_NO_OTHER_WRITERS=1'; exit 2; }
	$(POETRY) run python -m scripts.case_explanations.controller run --flow-id "$(PRODUCT_FLOW_ID)" --explanation-id "$(PRODUCT_EXPLANATION_ID)" --limit "$(PRODUCT_CASES_LIMIT)" --no-other-writers

## Verify the pinned case report offline without loading a model
product-explanations-verify:
	@test -n "$(PRODUCT_EXPLANATION_SHA256)" || { echo 'Set PRODUCT_EXPLANATION_SHA256 to the printed report digest'; exit 2; }
	$(POETRY) run python -m scripts.case_explanations.controller verify --explanation-id "$(PRODUCT_EXPLANATION_ID)" --expected-manifest-sha256 "$(PRODUCT_EXPLANATION_SHA256)"

## Read the existing case details enriched with verified SHAP contributions
product-explanations:
	@test -n "$(PRODUCT_EXPLANATION_SHA256)" || { echo 'Set PRODUCT_EXPLANATION_SHA256'; exit 2; }
	$(POETRY) run python -m scripts.case_explanations.controller cases --explanation-id "$(PRODUCT_EXPLANATION_ID)" --expected-manifest-sha256 "$(PRODUCT_EXPLANATION_SHA256)"
