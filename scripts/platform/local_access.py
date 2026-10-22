"""Foreground access to owned KinD services using their existing scoped forwards."""

import argparse
from contextlib import contextmanager
import json
import subprocess
import time


@contextmanager
def connection(service):
    from scripts.platform import kind_foundation as foundation

    foundation.owner()
    if service == "mlflow":
        from scripts.platform import kind_tracking as tracking

        tracking.inventory()
        with tracking.port_forward() as url:
            yield {"service": service, "url": url}
    elif service == "api":
        from scripts.platform import kind_serving as serving
        from scripts.platform import kind_serving_delivery as delivery

        plan = delivery.plan()
        observed = serving.observe(plan)
        with delivery.forward(observed["pods"]["gateway"]["name"]) as url:
            checked = serving.wait_route(url, plan, "blue")
            yield {"service": service, "url": url, "verification": checked}
    else:
        raise ValueError("Only mlflow and api local access are supported")


def open_local(service):
    with connection(service) as result:
        print(json.dumps(result, indent=2), flush=True)
        print("Ctrl+C encerra somente este acesso local.", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("mlflow", "api"))
    args = parser.parse_args()
    try:
        open_local(args.service)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, "Local access stopped: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
