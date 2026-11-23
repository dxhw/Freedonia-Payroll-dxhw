"""Loads and selects versioned Freedonia law definitions."""

import json
from datetime import date
from itertools import groupby
from pathlib import Path

from .models import FreedoniaLaw, LawAmendment


class NoApplicableLawError(LookupError):
    """No loaded law version was in force on the payment date."""


def _build_versions(
    sources: list[FreedoniaLaw | LawAmendment],
) -> list[FreedoniaLaw]:
    """Return one complete law per start date, given sources in date order."""
    laws: list[FreedoniaLaw] = []
    # we handle grouping in case there are multiple amendments made effective on the same day
    for effective_from, same_day in groupby(sources, key=lambda item: item.effective_from):
        group = list(same_day)
        if any(isinstance(source, FreedoniaLaw) for source in group):
            # A complete law replaces everything before it, so nothing else can start that day.
            if len(group) > 1:
                raise ValueError(
                    f"Duplicate Freedonia law effective date: {effective_from.isoformat()}"
                )
            laws.append(group[0])
            continue
        if not laws:
            raise ValueError(
                f"Freedonia law amendment in force from {effective_from.isoformat()} "
                "has no earlier version to amend"
            )
        laws.append(_apply_amendments(laws[-1], effective_from, group))
    return laws


def _apply_amendments(
    law: FreedoniaLaw,
    effective_from: date,
    amendments: list[LawAmendment],
) -> FreedoniaLaw:
    changes: dict[str, str] = {}
    for amendment in amendments:
        for path, value in amendment.changes.items():
            if changes.get(path, value) != value:
                # no contradictory amendments made effective on the same day
                raise ValueError(
                    f"Freedonia law amendments in force from {effective_from.isoformat()} "
                    f"set {path} to different values"
                )
            changes[path] = value

    data = law.model_dump(mode="json")
    data["effective_from"] = effective_from.isoformat()
    for path, value in changes.items():
        _replace_value(data, path, value, effective_from)
    return FreedoniaLaw.model_validate(data)


def _replace_value(data: dict, path: str, value: str, effective_from: date) -> None:
    """Replace the value already at a dotted ``path``; list items are named by index."""
    amendment = f"Freedonia law amendment in force from {effective_from.isoformat()}"
    keys = path.split(".")
    node: dict | list = data
    for depth, key in enumerate(keys):
        if isinstance(node, list) and key.isascii() and key.isdigit() and int(key) < len(node):
            slot: int | str = int(key)
        elif isinstance(node, dict) and key in node:
            slot = key
        else:
            raise ValueError(f"{amendment} changes a path that does not exist: {path}")
        if depth < len(keys) - 1:
            node = node[slot]
            continue
        if isinstance(node[slot], (dict, list)):
            raise ValueError(f"{amendment} must change a single value, not {path}")
        node[slot] = value


class LawRepository:
    def __init__(self, law_directory: Path | None = None):
        self._law_directory = law_directory or (
            Path(__file__).resolve().parent.parent / "laws"
        )
        self._sources: list[FreedoniaLaw | LawAmendment] = []
        self._laws: list[FreedoniaLaw] = []

    def load_all(self) -> list[FreedoniaLaw]:
        """Load every *.json file in the law directory."""
        paths = sorted(self._law_directory.glob("*.json"))
        if not paths:
            raise ValueError(
                f"No Freedonia law files found in {self._law_directory}"
            )
        # Read every file before building, because file names need not sort by date.
        self._add_sources([self._read_source(path.name) for path in paths])
        return list(self._laws)

    def _read_source(self, filename: str) -> FreedoniaLaw | LawAmendment:
        path = self._law_directory / filename
        if not path.is_file():
            raise ValueError(f"Freedonia law configuration not found: {filename}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Could not read law configuration {filename}: {exc}") from exc
        if isinstance(data, dict) and "changes" in data:
            return LawAmendment.model_validate(data)
        return FreedoniaLaw.model_validate(data)

    def _add_sources(self, new_sources: list[FreedoniaLaw | LawAmendment]) -> None:
        sources = sorted(
            [*self._sources, *new_sources], key=lambda item: item.effective_from
        )
        laws = _build_versions(sources)
        self._sources = sources
        self._laws = laws

    def get_law(self, payment_date: date) -> FreedoniaLaw:
        applicable = [
            law for law in self._laws
            if law.effective_from <= payment_date
        ]
        if not applicable:
            raise NoApplicableLawError(
                f"No applicable Freedonia law found for {payment_date.isoformat()}"
            )
        # choose the most recent applicable law
        return max(applicable, key=lambda item: item.effective_from)
