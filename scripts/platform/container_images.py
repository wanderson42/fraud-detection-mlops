"""Read one image identity from the dedicated KinD node through CRI."""

import json

from scripts.platform import kind_foundation as f


def inspect_image(tag):
    return json.loads(
        f.run(
            [
                "docker",
                "exec",
                f.CLUSTER + "-control-plane",
                "crictl",
                "--runtime-endpoint",
                "unix:///run/containerd/containerd.sock",
                "inspecti",
                tag,
            ]
        ).stdout
    )["status"]
