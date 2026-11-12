"""Small Linux/VirtualBox 7.2 frontend. Never adopt, start or delete a VM."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import uuid


def require(condition, message):
    if not condition:
        raise ValueError(message)


def vbox(*args):
    result = subprocess.run(
        ["VBoxManage", *map(str, args)], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise ValueError(f"VBoxManage {args[0]} failed; inspect VirtualBox locally")
    return result.stdout.strip()


def inventory():
    machines = []
    for line in vbox("list", "vms").splitlines():
        match = re.fullmatch(r'"(.*)" \{([0-9a-fA-F-]{36})\}', line)
        require(match is not None, "Cannot parse registered VM inventory")
        machines.append((match[1], str(uuid.UUID(match[2]))))
    return machines


def reserved_ports(machines):
    ports = set()
    for _, machine_id in machines:
        info = vbox("showvminfo", machine_id, "--machinereadable")
        for line in info.splitlines():
            if line.startswith("Forwarding("):
                # A conservative reservation also covers stopped machines.
                rule = json.loads(line.partition("=")[2]).split(",")
                require(len(rule) == 6, "Cannot parse existing NAT forwarding rule")
                if rule[1].lower() == "tcp":
                    ports.add(int(rule[3]))
    return ports


def free_port(requested, reserved):
    require(requested == 0 or 1024 <= requested <= 65535, "SSH port must be 0 or 1024..65535")
    require(requested == 0 or requested not in reserved, "SSH port reserved by another VM")
    for _ in range(64):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", requested))
            port = probe.getsockname()[1]
        if port not in reserved:
            return port
    raise ValueError("Cannot find an unreserved SSH port")


def prepare(args):
    require(sys.platform == "linux" and platform.machine() == "x86_64", "Linux amd64 only")
    version = vbox("--version")
    require(version.startswith("7.2."), "This delivery targets VirtualBox 7.2.x")
    require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", args.name), "Invalid VM name")
    require(1 <= args.cpus <= (os.cpu_count() or 1), "CPU count exceeds host capacity")
    require(2048 <= args.memory_mib <= 32768, "Memory must be 2048..32768 MiB")
    require(20 <= args.disk_gib <= 512, "Disk must be 20..512 GiB")
    base = Path(args.base).expanduser().resolve(strict=True)
    require(base.is_dir(), "Base must be an existing directory")
    target = base / args.name
    require(not os.path.lexists(target), "VM directory already exists; nothing will be adopted")
    iso = Path(args.iso).expanduser()
    require(iso.is_file() and not iso.is_symlink(), "ISO must be a regular non-symlink file")
    iso = iso.resolve()
    require(re.fullmatch(r"[0-9a-fA-F]{64}", args.iso_sha256), "Provide the official ISO SHA256")
    with iso.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    require(digest == args.iso_sha256.lower(), "ISO checksum mismatch")
    machines = inventory()
    require(args.name not in {name for name, _ in machines}, "VM name already registered")
    port = free_port(args.ssh_port, reserved_ports(machines))
    require(
        shutil.disk_usage(base).free >= (args.disk_gib + 2) * 1024**3,
        "Insufficient disk budget for a fully grown disk plus 2 GiB",
    )
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(memory["MemAvailable"].split()[0]) // 1024
    machine_id = str(uuid.uuid4())
    disk = str(target / "system.vdi")
    commands = [
        [
            "createvm",
            "--name",
            args.name,
            "--ostype",
            "Ubuntu24_64",
            "--basefolder",
            str(base),
            "--uuid",
            machine_id,
            "--register",
        ],
        [
            "modifyvm",
            machine_id,
            "--cpus",
            str(args.cpus),
            "--memory",
            str(args.memory_mib),
            "--ioapic",
            "on",
            "--graphicscontroller",
            "vmsvga",
            "--vram",
            "32",
            "--nic1",
            "nat",
            "--nat-pf1",
            f"ssh,tcp,127.0.0.1,{port},,22",
            "--boot1",
            "dvd",
            "--boot2",
            "disk",
            "--boot3",
            "none",
            "--boot4",
            "none",
        ],
        [
            "createmedium",
            "disk",
            "--filename",
            disk,
            "--size",
            str(args.disk_gib * 1024),
            "--format",
            "VDI",
            "--variant",
            "Standard",
        ],
        ["storagectl", machine_id, "--name", "SATA", "--add", "sata", "--controller", "IntelAhci"],
        [
            "storageattach",
            machine_id,
            "--storagectl",
            "SATA",
            "--port",
            "0",
            "--device",
            "0",
            "--type",
            "hdd",
            "--medium",
            disk,
        ],
        [
            "storageattach",
            machine_id,
            "--storagectl",
            "SATA",
            "--port",
            "1",
            "--device",
            "0",
            "--type",
            "dvddrive",
            "--medium",
            str(iso),
        ],
    ]
    return {
        "status": "vm_creation_preview",
        "name": args.name,
        "uuid": machine_id,
        "directory": str(target),
        "virtualbox_version": version,
        "iso_sha256": digest,
        "ssh_host": "127.0.0.1",
        "ssh_port": port,
        "cpus": args.cpus,
        "memory_mib": args.memory_mib,
        "disk_gib": args.disk_gib,
        "host_available_memory_mib": available,
        "boot_budget_ready": available >= args.memory_mib + 2048,
        "port_reserved_until_boot": False,
        "commands": commands,
        "guest_installed": False,
        "vm_started": False,
        "native_acceptance": False,
    }


def create(args):
    # Linux directory lock serializes this helper without creating lock files.
    base = Path(args.base).expanduser().resolve(strict=True)
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = prepare(args)
        try:
            for command in plan["commands"]:
                vbox(*command)
            info = vbox("showvminfo", plan["uuid"], "--machinereadable")
            require('VMState="poweroff"' in info, "New VM is not powered off")
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"Creation interrupted for UUID {plan['uuid']} at {plan['directory']}; "
                "preserve partial resources and inspect locally; no automatic cleanup"
            ) from exc
        plan["status"] = "vm_created_powered_off"
        return plan
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "create"])
    parser.add_argument("--name", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--iso", required=True)
    parser.add_argument("--iso-sha256", required=True)
    parser.add_argument("--cpus", type=int, default=6)
    parser.add_argument("--memory-mib", type=int, default=8192)
    parser.add_argument("--disk-gib", type=int, default=80)
    parser.add_argument("--ssh-port", type=int, default=0)
    args = parser.parse_args()
    try:
        result = create(args) if args.action == "create" else prepare(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"VM provisioning stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
