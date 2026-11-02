import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from live import store


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    store.create_tables(eng)
    return eng


@pytest.fixture(autouse=True)
def forget_failures():
    from live import schedules
    schedules._failed_at.clear()
