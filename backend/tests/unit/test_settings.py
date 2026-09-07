from pathlib import Path

import pytest

from app.settings import Settings


def test_settings_reads_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.sqlite")
    s = Settings()
    assert s.database_url == "sqlite:///./test.sqlite"
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


def test_corporate_actions_path_does_not_depend_on_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The runbook starts the API from `backend/` and the CLI runs from wherever
    the operator happens to be. A CWD-relative default points at a directory that
    does not exist, and the failure mode is the worst kind: the import refuses,
    because an unreadable resolutions file answers nothing, and says only that the
    corporate actions are unanswered -- while the file sits there, answered.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("CORPORATE_ACTIONS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    resolved = Path(Settings().corporate_actions_path)
    assert resolved.is_absolute()
    assert resolved.parent.name == "config"
    assert (resolved.parent.parent / "backend" / "app" / "settings.py").exists()


def test_corporate_actions_path_is_still_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CORPORATE_ACTIONS_PATH", "/tmp/answers.yaml")
    assert Settings().corporate_actions_path == "/tmp/answers.yaml"


def test_the_symbol_and_price_answer_files_default_beside_the_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absolute, anchored on the repo, for the reason `corporate_actions_path`
    is: the API starts from `backend/` and the CLI runs from wherever the
    operator happens to be. A relative default breaks in the worst way -- an
    unreadable answers file answers nothing, so `fetch-prices` refuses and
    reports every instrument as unresolved while the file sits there, answered,
    one directory up."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    settings = Settings()
    assert Path(settings.instrument_symbols_path).is_absolute()
    assert Path(settings.instrument_symbols_path).name == "instrument_symbols.yaml"
    assert Path(settings.manual_prices_path).is_absolute()
    assert Path(settings.manual_prices_path).name == "manual_prices.csv"
