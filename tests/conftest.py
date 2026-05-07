"""Shared pytest fixtures."""

import shutil
import kuzu
import pytest
from pathlib import Path

from context_compiler.indexer.indexer import index_repository
from context_compiler.indexer.graph import open_database


PYTHON_FIXTURE = Path(__file__).parent / "fixtures" / "python_repo"
TS_FIXTURE = Path(__file__).parent / "fixtures" / "typescript_repo"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


# Index once per session — open_database is cheap, index_repository is not
@pytest.fixture(scope="session")
def _python_db(tmp_path_factory):
    repo = tmp_path_factory.mktemp("python_repo") / "repo"
    shutil.copytree(PYTHON_FIXTURE, repo)
    index_repository(repo, verbose=False)
    return open_database(repo)


@pytest.fixture(scope="session")
def _ts_db(tmp_path_factory):
    repo = tmp_path_factory.mktemp("ts_repo") / "repo"
    shutil.copytree(TS_FIXTURE, repo)
    index_repository(repo, verbose=False)
    return open_database(repo)


# Fresh connection per test (no state accumulation)
@pytest.fixture
def python_conn(_python_db):
    return kuzu.Connection(_python_db)


@pytest.fixture
def ts_conn(_ts_db):
    return kuzu.Connection(_ts_db)
