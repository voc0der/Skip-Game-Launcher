import pytest

from helpers import load_script


@pytest.fixture(scope="session")
def resolve_mod():
    return load_script("resolve")


@pytest.fixture(scope="session")
def verify_mod():
    return load_script("verify")


@pytest.fixture
def games():
    """A small manifest standing in for games.json."""
    return [
        {"name": "Portal 2", "out": "Portal2.exe", "stores": {"steam": "620"}},
        {"name": "Overwatch", "out": "Overwatch.exe", "stores": {"battlenet": "Pro"}},
        {
            "name": "Metro 2033 Redux",
            "out": "Metro2033Redux.exe",
            "stores": {"epic": "Petunia", "steam": "286690"},
        },
    ]


@pytest.fixture
def form():
    """Build an issue-form body the way GitHub renders one."""

    def _build(name="Some Game", store="Steam", app_id=None, filename=None):
        def field(label, value):
            return f"### {label}\n\n{value if value else '_No response_'}\n\n"

        return (
            field("Game name", name)
            + field("Store", store)
            + field("App ID / product code", app_id)
            + field("Output filename", filename)
        )

    return _build
