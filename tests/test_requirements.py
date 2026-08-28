from __future__ import annotations

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _declared_dependencies() -> set[str]:
    data = tomllib.load((ROOT / "pyproject.toml").open("rb"))
    dependencies: list[str] = data["project"]["dependencies"]
    return set(dependencies)


def _pinned_dependencies() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def test_requirements_matches_pyproject() -> None:
    """requirements.txt hanya bayangan pyproject, dipakai Docker.

    Menyimpan dua daftar dependensi berarti keduanya bisa berbeda diam-diam, dan yang
    ketahuan belakangan biasanya saat image produksi gagal jalan. Test ini yang menjaga
    keduanya tetap sama.
    """
    declared = _declared_dependencies()
    pinned = _pinned_dependencies()

    assert pinned == declared, (
        "requirements.txt tidak sama dengan pyproject.toml. "
        f"Hanya di pyproject: {sorted(declared - pinned)}. "
        f"Hanya di requirements: {sorted(pinned - declared)}."
    )
