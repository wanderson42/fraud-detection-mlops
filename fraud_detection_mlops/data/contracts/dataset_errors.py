"""Stable dataset errors shared by contracts and acquisition/publication."""


class BronzeError(Exception):
    """An acquisition, integrity or provenance check failed."""


class SilverError(ValueError):
    """A dataset cannot satisfy the supported Silver contract."""


class GoldError(ValueError):
    """A modeling dataset fails the supported Gold contract."""
