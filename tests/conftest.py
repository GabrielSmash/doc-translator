import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--corpus",
        action="store_true",
        help="also measure the public corpus (downloads ~3.4 MB once)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--corpus"):
        return
    skip = pytest.mark.skip(reason="needs --corpus")
    for item in items:
        if "corpus" in item.keywords:
            item.add_marker(skip)
