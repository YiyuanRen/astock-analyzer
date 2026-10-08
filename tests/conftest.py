import pytest

from app.models import database


@pytest.fixture
def tmp_db(tmp_path):
    """每个测试用独立的临时 SQLite 库。"""
    database.set_db_path(tmp_path / "test.db")
    database.init_db()
    yield
    database.set_db_path(None)
