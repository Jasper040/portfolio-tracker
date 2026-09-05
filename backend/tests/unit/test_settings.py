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
