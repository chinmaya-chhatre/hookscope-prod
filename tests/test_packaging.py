import tomllib
from pathlib import Path

from hookscope import __version__


def test_package_metadata_matches_runtime_and_requirements():
    root = Path(__file__).parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == __version__
    assert project["scripts"]["hookscope"] == "hookscope.cli:main"
    assert set(project["dependencies"]) == set((root / "requirements.txt").read_text().splitlines())
    assert metadata["tool"]["setuptools"]["package-data"]["hookscope"] == ["templates/*.html"]
