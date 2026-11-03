import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.samples import CATALOG_CSV
from tests.transport import ScriptedTransport


@pytest.fixture
def transport():
    script = ScriptedTransport()
    script.respond("GET", "ourairports-data/airports.csv", text=CATALOG_CSV)
    return script


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite://",
        opensky_client_id="",
        opensky_client_secret="",
        ourairports_url="https://davidmegginson.github.io/ourairports-data/airports.csv",
    )


@pytest.fixture
def app(settings, transport):
    return create_app(settings, transport=transport, sync_on_startup=False)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
