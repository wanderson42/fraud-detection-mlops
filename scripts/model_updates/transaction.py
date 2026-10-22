"""Journalled traffic/alias transition with explicit approval and compensation."""

from scripts.operational_round.policy import require


def restore(journal, backend, persist):
    """Refuse third-party changes; restore the old route before its Registry alias."""
    old, new = journal["before"], journal["after"]
    try:
        require(
            backend.alias() in (old["version"], new["version"]), "Foreign alias during recovery"
        )
        require(backend.route() in (old["slot"], new["slot"]), "Foreign route during recovery")
        backend.prepare_recovery(old)
        journal["phase"] = "restoring_previous_route"
        persist(journal)
        backend.set_route(old["slot"])
        backend.check(old)
        observed = backend.alias()
        require(observed in (old["version"], new["version"]), "Concurrent alias during recovery")
        if observed != old["version"]:
            backend.set_alias(old["version"], observed)
        backend.check(old)
        require(backend.alias() == old["version"], "Previous alias not restored")
        backend.finalize(old)
        journal["phase"] = "rolled_back"
        persist(journal)
    except Exception as exc:
        journal["phase"] = "recovery_required"
        journal["recovery_error"] = str(exc)[-1000:]
        persist(journal)
        raise
    return journal


def activate(intent, backend, persist, *, approval, no_other_writers):
    require(no_other_writers, "Declare no other Registry/route writers")
    require(approval == intent["approval_sha256"], "Approve the exact staged release digest")
    require(
        backend.alias() == intent["before"]["version"]
        and backend.route() == intent["before"]["slot"],
        "Active model changed since staging",
    )
    backend.check(intent["before"])
    journal = {**intent, "phase": "release_authorized"}
    persist(journal)
    try:
        journal["phase"] = "switching_traffic"
        persist(journal)
        backend.set_route(intent["after"]["slot"])
        backend.check(intent["after"])
        journal["phase"] = "updating_champion"
        persist(journal)
        backend.set_alias(intent["after"]["version"], intent["before"]["version"])
        require(backend.alias() == intent["after"]["version"], "New alias readback differs")
        backend.check(intent["after"])
        backend.finalize(intent["after"])
        journal["phase"] = "active"
        persist(journal)
    except Exception as exc:
        journal["release_error"] = str(exc)[-1000:]
        restore(journal, backend, persist)
        raise
    return journal
