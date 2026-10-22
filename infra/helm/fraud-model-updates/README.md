# Recurring model release chart

Keeps the existing fraud-detector release/owner and gateway. Runtime/carrier images are pinned per slot so staging does not restart the active pod. Explicit traffic and Registry approval lives in scripts/model_updates, not in Helm hooks.
