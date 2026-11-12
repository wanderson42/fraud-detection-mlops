"""Safety boundaries with fake VBoxManage; never require a host hypervisor."""

from argparse import Namespace
import hashlib
import json
import socket

import pytest
from scripts.vm_provision import controller as c


@pytest.fixture
def host(tmp_path, monkeypatch):
    iso = tmp_path / "ubuntu.iso"
    iso.write_bytes(b"synthetic installation media")
    args = Namespace(
        name="fraud-bootstrap-test",
        base=str(tmp_path),
        iso=str(iso),
        iso_sha256=hashlib.sha256(iso.read_bytes()).hexdigest(),
        cpus=2,
        memory_mib=8192,
        disk_gib=20,
        ssh_port=0,
    )
    calls = []

    def command(*argv):
        calls.append(argv)
        if argv == ("--version",):
            return "7.2.22r175245"
        if argv == ("list", "vms"):
            return '"existing" {7488814e-8f4d-494d-87b5-c8c2f1f7a963}'
        if argv[0] == "showvminfo":
            return 'VMState="poweroff"\nForwarding(0)="ssh,tcp,127.0.0.1,2223,,22"'
        return ""

    monkeypatch.setattr(c, "vbox", command)
    monkeypatch.setattr(c.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(c.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(c.shutil, "disk_usage", lambda _: Namespace(free=100 * 1024**3))
    return args, calls


def test_preview_never_mutates_and_reserves_no_files(host):
    args, calls = host
    before = set(c.Path(args.base).iterdir())
    plan = c.prepare(args)
    assert before == set(c.Path(args.base).iterdir())
    assert all(command[0] in {"--version", "list", "showvminfo"} for command in calls)
    assert plan["ssh_port"] != 2223
    assert plan["vm_started"] is False
    assert "127.0.0.1" in json.dumps(plan["commands"])


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("name", "existing", "already registered"),
        ("name", "../victim", "Invalid VM name"),
        ("iso_sha256", "0" * 64, "checksum mismatch"),
        ("ssh_port", 2223, "reserved by another VM"),
        ("cpus", 17, "CPU count"),
    ],
)
def test_rejections_before_any_mutation(host, field, value, reason):
    args, calls = host
    setattr(args, field, value)
    with pytest.raises(ValueError, match=reason):
        c.create(args)
    assert not any(command[0] == "createvm" for command in calls)


def test_existing_unregistered_directory_is_preserved(host):
    args, calls = host
    target = c.Path(args.base) / args.name
    target.mkdir()
    (target / "private-data").write_text("keep")
    with pytest.raises(ValueError, match="directory already exists"):
        c.create(args)
    assert (target / "private-data").read_text() == "keep"
    assert not any(command[0] == "createvm" for command in calls)


def test_live_port_collision_refused(host):
    args, _ = host
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        args.ssh_port = listener.getsockname()[1]
        with pytest.raises(OSError):
            c.create(args)


def test_creation_targets_only_its_new_uuid_and_never_starts(host):
    args, calls = host
    result = c.create(args)
    own = result["uuid"]
    for command in calls:
        if command[0] in {"modifyvm", "storagectl", "storageattach"}:
            assert command[1] == own
    assert not any(command[0] in {"startvm", "controlvm", "unregistervm"} for command in calls)
    assert result["status"] == "vm_created_powered_off"


def test_partial_creation_preserved_without_destructive_cleanup(host, monkeypatch):
    args, calls = host
    original = c.vbox

    def fail(*command):
        if command[0] == "createmedium":
            raise ValueError("synthetic storage failure")
        return original(*command)

    monkeypatch.setattr(c, "vbox", fail)
    with pytest.raises(ValueError, match="preserve partial resources"):
        c.create(args)
    assert any(command[0] == "createvm" for command in calls)
    assert not any(command[0] == "unregistervm" for command in calls)


def test_disk_budget_blocks_creation(host, monkeypatch):
    args, calls = host
    monkeypatch.setattr(c.shutil, "disk_usage", lambda _: Namespace(free=1024))
    with pytest.raises(ValueError, match="disk budget"):
        c.create(args)
    assert not any(command[0] == "createvm" for command in calls)


def test_partial_inventory_is_not_treated_as_empty(host, monkeypatch):
    args, _ = host
    original = c.vbox
    monkeypatch.setattr(
        c,
        "vbox",
        lambda *argv: "unexpected output" if argv == ("list", "vms") else original(*argv),
    )
    with pytest.raises(ValueError, match="inventory"):
        c.create(args)
