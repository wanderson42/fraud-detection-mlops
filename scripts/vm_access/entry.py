"""Read Make-exported configuration as data, without shell interpolation."""

import os
import sys

from scripts.vm_access.client import main


def arguments(argv, env):
    result = [*argv, "--profile", env.get("VM_PROFILE", "default")]
    if argv and argv[0] == "up":
        for key, option in (
            ("VM_HOST", "--host"),
            ("VM_USER", "--user"),
            ("VM_SSH_PORT", "--ssh-port"),
            ("VM_ROOT", "--remote-root"),
            ("VM_SERVICES", "--services"),
        ):
            if env.get(key):
                result += [option, env[key]]
        for key, option in (
            ("VM_LOCAL_PORTS", "--local-port"),
            ("VM_REMOTE_PORTS", "--remote-port"),
        ):
            for assignment in filter(None, env.get(key, "").split(",")):
                result += [option, assignment]
    return result


if __name__ == "__main__":
    sys.argv[1:] = arguments(sys.argv[1:], os.environ)
    main()
