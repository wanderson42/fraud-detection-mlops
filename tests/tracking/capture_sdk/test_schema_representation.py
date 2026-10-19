"""Real PG17 dump descriptor regressions; exact definitions outside five cast pairs."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from scripts.tracking import capture_worker as archive
from scripts.tracking import restore_worker as worker


@pytest.fixture
def descriptors():
    path = Path(__file__).parent / "fixtures/pg17_schema_representations.json"
    return json.loads(path.read_text())


def test_pg17_dump_casts_and_dropped_column_slot_preserve_logical_schema(descriptors):
    source, restored = descriptors["source"], descriptors["restored"]
    before = deepcopy(descriptors)
    assert source != restored
    assert worker.canonical_metadata(source) == worker.canonical_metadata(restored)
    assert descriptors == before  # Archive/control inputs are immutable.
    assert len(source["constraints"]) == len(worker.CHECK_CAST_EQUIVALENTS) == 5
    # A query's inter-table/constraint ordering does not define schema order.
    restored = {k: list(reversed(v)) for k, v in restored.items()}
    assert worker.canonical_metadata(source) == worker.canonical_metadata(restored)


@pytest.mark.parametrize("field,value", [(2, "integer"), (3, "int4"), (4, "YES"), (5, "1")])
def test_type_nullability_and_default_changes_are_rejected(descriptors, field, value):
    changed = deepcopy(descriptors["restored"])
    changed["columns"][0][field] = value
    assert worker.canonical_metadata(descriptors["source"]) != worker.canonical_metadata(changed)


@pytest.mark.parametrize(
    "change",
    [
        "column_order",
        "missing_column",
        "extra_column",
        "rule",
        "missing_rule",
        "extra_rule",
        "rule_type",
        "rule_identity",
    ],
)
def test_other_structural_changes_are_rejected(descriptors, change):
    changed = deepcopy(descriptors["restored"])
    columns, rules = changed["columns"], changed["constraints"]
    if change == "column_order":
        columns[0][-1], columns[1][-1] = columns[1][-1], columns[0][-1]
    elif change == "missing_column":
        columns.pop()
    elif change == "extra_column":
        columns.append(["spans", "extra", "integer", "int4", "YES", None, 99])
    elif change == "rule":
        rules[0][-1] = rules[0][-1].replace("'active'", "'other'")
    elif change == "missing_rule":
        rules.pop()
    elif change == "extra_rule":
        rules.append(["spans", "extra_check", "c", "CHECK (1 = 0)"])
    elif change == "rule_type":
        rules[0][2] = "f"
    else:
        rules[0][1] += "_unreviewed"
    assert worker.canonical_metadata(descriptors["source"]) != worker.canonical_metadata(changed)


def test_unreviewed_check_and_foreign_key_are_literal():
    for kind, rule in [("c", "CHECK (id > 0)"), ("f", "FOREIGN KEY (id) REFERENCES parent(id)")]:
        source = {"constraints": [["child", "unknown_rule", kind, rule]]}
        target = deepcopy(source)
        target["constraints"][0][-1] += " NOT VALID"
        assert worker.canonical_metadata(source) != worker.canonical_metadata(target)


@pytest.mark.parametrize("section", ["columns", "constraints"])
def test_duplicate_descriptors_fail_closed(descriptors, section):
    changed = deepcopy(descriptors["source"])
    changed[section].append(changed[section][0])
    with pytest.raises(ValueError, match="Duplicate"):
        worker.canonical_metadata(changed)


@pytest.mark.parametrize(
    "field",
    ["tables", "sequences", "indexes", "locations", "alembic_revisions", "logical_bytes_read"],
)
def test_all_other_inventory_fields_remain_exact(descriptors, field):
    source, target = deepcopy(descriptors["source"]), deepcopy(descriptors["restored"])
    source[field] = ["original"]
    target[field] = ["changed"]
    assert worker.canonical_metadata(source) != worker.canonical_metadata(target)


def test_readback_uses_schema_policy_but_preserves_archive_and_all_data_checks(
    tmp_path, monkeypatch, descriptors
):
    source, target = deepcopy(descriptors["source"]), deepcopy(descriptors["restored"])
    source["tables"] = target["tables"] = {"metrics": {"rows": 44, "sha256": "unchanged"}}
    archive.save(tmp_path / "metadata.json", source)
    archive.save(tmp_path / "objects.json", [])
    before = (tmp_path / "metadata.json").read_bytes()
    monkeypatch.setattr(archive, "object_listing", lambda client: [])
    from contextlib import nullcontext

    monkeypatch.setattr(worker, "connect", nullcontext)
    monkeypatch.setattr(archive, "database_inventory", lambda connection: target)
    result = worker.verify_data(tmp_path, object())
    assert result["database_unchanged"] is True
    assert result["raw_descriptor_fields_differing"] == ["columns", "constraints"]
    assert (tmp_path / "metadata.json").read_bytes() == before
    target["tables"] = {"metrics": {"rows": 43, "sha256": "changed"}}
    with pytest.raises(ValueError, match="contents differ"):
        worker.verify_data(tmp_path, object())
