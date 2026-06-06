"""Тестовый PR для проверки CI pipeline."""


def test_ci_pipeline_runs() -> None:
    """CI должен подхватить этот тест и показать зелёный результат."""
    assert 1 + 1 == 2


def test_python_version() -> None:
    import sys

    assert sys.version_info >= (3, 11)
