"""Read numbers back out of the generated reports, rather than copying them.

An accuracy figure hand-copied into an interface is a figure that goes stale
the next time the model is refitted, and a stale accuracy claim on a map is
worse than no claim at all: the reader has no way to know it drifted. So the
interface parses `transferability*.md` instead of carrying its own copy.

Parsing a generated markdown table is fragile, and it has already failed once.
`transferability.md` ends with a *second* table - training rows kept per model -
whose rows also start with a species name, also say `maxent-style`, and whose
third cell is a row count. Scanning the whole file let that table win and the
interface displayed "3051.00 AUC". Two defences, both kept here:

* anchor on the `## Result` heading and read only that section, and
* refuse any AUC outside [0, 1] loudly, rather than shipping it.

`scripts/export_web_preview.py` still carries its own copy of this logic for
the Kristiansand payload. That is duplication and should be reconciled, but
converting it means regenerating a Phase 0 artefact to prove the payload came
out identical, which is a separate piece of work from adding the national one.
"""
from __future__ import annotations

import pathlib


def _rows(path: pathlib.Path, heading: str):
    """Table rows under a `## heading`, as lists of stripped cells."""
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("##"):
            section = line.lstrip("# ").strip().lower()
            continue
        if section != heading or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2 and "---" not in cells[0]:
            yield cells


def transfer_aucs(path: pathlib.Path, model: str = "maxent-style"
                  ) -> tuple[dict[str, dict], float | None]:
    """`({scientific name: {auc, ci}}, null AUC)` from a transferability report.

    Raises rather than returning a number outside [0, 1]: a layout change
    should stop the build, not quietly reach a user as an accuracy claim.
    """
    out: dict[str, dict] = {}
    null_auc = None
    if not path.exists():
        return out, None
    for cells in _rows(path, "result"):
        if len(cells) < 4 or model not in cells[1]:
            continue
        try:
            auc = float(cells[2])
        except ValueError:
            continue
        if not 0.0 <= auc <= 1.0:
            raise ValueError(
                f"{path.name}: AUC {auc} for {cells[0]} is not a probability. "
                f"The report layout has changed; fix the parser rather than "
                f"shipping the number.")
        if cells[0].startswith("SYNTHETIC"):
            null_auc = auc
            continue
        out[cells[0]] = {"auc": auc, "ci": cells[3]}
    return out, null_auc


def held_out_counts(path: pathlib.Path) -> dict[str, int]:
    """`{scientific name: held-out record count}` from the counts table.

    Without it, 0.63 from 160 records and 0.63 from 7 look identical in a
    species picker, and the second is close to meaningless.
    """
    out: dict[str, int] = {}
    if not path.exists():
        return out
    for cells in _rows(path, "held-out record counts"):
        if len(cells) < 3 or cells[0].lower() == "set":
            continue
        try:
            out[cells[0]] = int(cells[2].replace(",", ""))
        except ValueError:
            continue
    return out
