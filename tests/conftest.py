"""Shared fixtures. Imports live inside fixtures so each module's tests only
depend on the modules they exercise."""

from __future__ import annotations

import pytest

from emporio.config import PROJECT_ROOT, Clock, parse_store_datetime

DATA_DIR = PROJECT_ROOT / "data"
# The dataset is a snapshot from late March 2026 (latest order: 2026-03-22).
DEMO_NOW = "2026-03-24T10:30"  # a Tuesday, during opening hours


@pytest.fixture(scope="session")
def data():
    from emporio.data import load_store_data

    return load_store_data(DATA_DIR)


@pytest.fixture(scope="session")
def catalog(data):
    from emporio.catalog import Catalog

    return Catalog(data)


@pytest.fixture(scope="session")
def policies():
    from emporio.policies import POLICY_FILE, PolicyManual

    return PolicyManual.from_pdf(DATA_DIR / POLICY_FILE)


@pytest.fixture
def clock() -> Clock:
    return Clock(parse_store_datetime(DEMO_NOW))


@pytest.fixture
def storage():
    from emporio.storage import Storage

    return Storage(":memory:")


@pytest.fixture
def toolbox(data, catalog, policies, storage, clock):
    from emporio.orders import OrderDesk
    from emporio.tools import Toolbox

    return Toolbox(data, catalog, policies, OrderDesk(data), storage, clock)


@pytest.fixture
def ctx():
    from emporio.tools import SessionContext

    return SessionContext(session_id="test")
