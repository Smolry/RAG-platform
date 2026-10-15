from app.observability.db import _database_url


def test_bare_postgresql_url_uses_declared_psycopg2_driver():
    url = _database_url("postgresql://rag:secret@localhost:5432/rag")

    assert url.drivername == "postgresql+psycopg2"
    assert url.password == "secret"


def test_explicit_postgresql_driver_is_preserved():
    url = _database_url("postgresql+psycopg://rag@localhost/rag")

    assert url.drivername == "postgresql+psycopg"


def test_sqlite_url_is_preserved():
    url = _database_url("sqlite:///:memory:")

    assert url.drivername == "sqlite"
    assert url.database == ":memory:"
