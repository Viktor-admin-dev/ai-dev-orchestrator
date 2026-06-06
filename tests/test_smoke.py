"""Smoke test — проверяет, что пакет импортируется и CI работает."""

from orchestrator import __version__


def test_version_exists() -> None:
    assert __version__ is not None


def test_version_format() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    for part in parts:
        assert part.isdigit()
