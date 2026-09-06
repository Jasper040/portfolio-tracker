import pytest

from app.settings import Settings


def test_settings_reads_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.sqlite")
    s = Settings()
    assert s.database_url == "sqlite:///./test.sqlite"
    assert s.base_currency == "EUR"
    assert s.lot_method == "FIFO"


def test_settings_fails_loudly_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_cors_origins_defaults_to_the_vite_dev_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings().cors_origin_list == ["http://localhost:5173"]


def test_cors_origins_splits_on_commas_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A list-typed field would demand JSON here and reject the obvious spelling.

    Whitespace is stripped and empty entries dropped so a trailing comma, or a
    value written with spaces after the commas, does not produce an origin of ""
    that silently matches nothing.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, http://localhost:5174 ,")
    assert Settings().cors_origin_list == [
        "http://localhost:5173",
        "http://localhost:5174",
    ]
