from pathlib import Path

import pytest

from reconloop import io
from reconloop.generate import generate


@pytest.fixture(scope="session")
def std_batch(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("std7")
    generate(d, seed=7, difficulty="standard")
    return d


@pytest.fixture(scope="session")
def hard_batch(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("hard8")
    generate(d, seed=8, difficulty="hard")
    return d


@pytest.fixture()
def loaded(std_batch):
    return io.load_batch(std_batch), io.load_truth(std_batch)
