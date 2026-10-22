"""Revise only the reviewed empty-artifact readback pin, preserving the original plan."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from scripts.platform import kind_foundation as f
from scripts.platform import kind_history, kind_tracking
from scripts.platform.history import registry, source

TRANSFER = "scripts/platform/history/transfer.py"
REVIEWED_PREVIOUS_SHA256 = "ec4abeb238bf34e080e19ce6f8cbf5174c3725de7b3f3035f8ab0b7588e5ef73"
REVIEWED_CURRENT_SHA256 = "c65ffcae8095042c31abdd0ee10a7d91e89e55fa9bbfab072f385f5f1da08e93"


def revise_value(value, current_pins, original_sha256):
    f.require(
        current_pins[TRANSFER] == REVIEWED_CURRENT_SHA256,
        "Transfer code differs from the reviewed empty-artifact fix",
    )
    previous = dict(current_pins)
    previous[TRANSFER] = REVIEWED_PREVIOUS_SHA256
    f.require(
        value["schema"] == "fraud_kind_history_transfer_plan_v1"
        and value["source_pins"] == previous,
        "Only the reviewed empty-artifact readback revision is supported",
    )
    result = copy.deepcopy(value)
    result["source_pins"] = current_pins
    result["plan_revision"] = {
        "reason": "empty_file_listing_size_absent_requires_downloaded_size_and_SHA256",
        "previous_plan_sha256": original_sha256,
        "previous_transfer_sha256": REVIEWED_PREVIOUS_SHA256,
        "current_transfer_sha256": current_pins[TRANSFER],
        "source_payload_unchanged": True,
    }
    return result


def revise(path, output):
    path, output = path.absolute(), output.absolute()
    for item in (path, output):
        f.managed_destination(item, "storage")
        f.require(item.parent == kind_history.work(), "Plan must be in private history workspace")
    f.require(
        path != output and path.is_file() and path.stat().st_mode & 0o077 == 0,
        "Preserve original private plan and select a distinct output",
    )
    raw = path.read_bytes()
    value = json.loads(raw)
    result = revise_value(value, kind_history.pins(), hashlib.sha256(raw).hexdigest())
    payload = value["payload"]
    checked = source.prepare(f.ROOT, payload["snapshot_bundle_id"], payload["release_bundle_id"])
    f.require(
        source.fingerprint(checked) == source.fingerprint(payload) == value["payload_sha256"],
        "Snapshot or original plan changed",
    )
    f.require(
        registry.references(f.ROOT, payload) == value["reference"],
        "Historical quality-gate reference changed",
    )
    f.require(
        f.owner()["node_uid"] == value["node_uid"]
        and kind_tracking.inventory()["deployment_uid"] == value["tracking_deployment_uid"],
        "Destination ownership changed",
    )
    f.require(path.read_bytes() == raw, "Original plan changed during revision")
    if output.exists():
        f.require(
            output.stat().st_mode & 0o077 == 0 and json.loads(output.read_bytes()) == result,
            "Existing output differs; preserve and review",
        )
    else:
        f.private_json(output, result)
    kind_history.read_plan(output)
    return {
        "status": "plan_revision_verified",
        "original_plan_preserved": True,
        "plan": str(output),
        "source_payload_unchanged": True,
        "destination_modified": False,
        "refit": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with f.exclusive():
            result = revise(args.plan, args.output)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, "Plan revision stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
