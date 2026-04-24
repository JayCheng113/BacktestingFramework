"""Shim to avoid importing the broken system readline extension during pytest startup."""


def parse_and_bind(*args, **kwargs):  # pragma: no cover
    return None


def read_history_file(*args, **kwargs):  # pragma: no cover
    return None


def write_history_file(*args, **kwargs):  # pragma: no cover
    return None


def set_history_length(*args, **kwargs):  # pragma: no cover
    return None
