"""Stable error types used by experimental execution and verification."""


class BaselineError(ValueError):
    """A run fails the fixed validation-only baseline contract."""


class ExperimentError(ValueError):
    """The declared experiment or its artifacts do not reconcile."""
