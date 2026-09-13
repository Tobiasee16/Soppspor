"""A variant run must never land on the file the writeup quotes.

This project has made the same mistake four times: a partial download replacing
a good one, a national climate report overwriting the Agder one, a tagged model
run replacing the baseline it was being compared against, and - found while
giving the transfer test a second extent - `run_transfer.py` writing
`transferability.md` on every invocation including tagged ones.

Each time the overwritten file looked completely normal afterwards. That is
what makes this class worth a test rather than a habit: there is no error, no
truncation and no obviously wrong number, just a comparison that quietly
reports no difference because both sides became the same file.

Two rules, checked for both scripts and both extents:

* untagged runs write the canonical file for their extent, and
* tagged runs write only into `runs/`, never the canonical file.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_models as rm
import run_transfer as rt


class FakeLayer:
    """Stands in for a RasterLayer without needing a raster on disk."""

    categories = None
    missing_indicator = False

    def __init__(self, names):
        self.names = names

    def resolve(self):
        return list(range(1, len(self.names) + 1)), list(self.names)

    def suffixes(self):
        return []


SPP = {
    "null_control": {"label": "SYNTHETIC null", "key": "null"},
    "species": [
        {"key": "a", "scientific_name": "Aa aa", "predicted_rank": 1},
        {"key": "b", "scientific_name": "Bb bb", "predicted_rank": 2},
        {"key": "c", "scientific_name": "Cc cc", "predicted_rank": 3},
    ],
}


@pytest.fixture
def processed(tmp_path, monkeypatch):
    """Redirect both scripts' output into a temporary tree."""
    out = tmp_path / "data" / "processed"
    out.mkdir(parents=True)
    monkeypatch.setattr(rm, "PROCESSED", out)
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    monkeypatch.setattr(rm, "ROOT", tmp_path)
    return out


def model_report(processed, tag):
    from src.features.stack import Design
    import numpy as np
    d = Design(X=np.zeros((2, 1)), y=np.array([1, 0]), coords=np.zeros((2, 2)),
               names=["x"], dropped={"presence": 0, "background": 0,
                                     "presence_pct": 0.0, "background_pct": 0.0})
    rows = [{"species": "Aa aa", "model": "lightgbm", "auc": 0.7, "auc_sd": 0.1,
             "boyce": 0.1, "boyce_sd": 0.1, "boyce_q": 0.1, "boyce_q_sd": 0.1,
             "warnings": []}]
    rm.write_report(rows, {}, {"Aa aa": d}, SPP, [FakeLayer(["x"])],
                    7500, 5, 4000, tag)


def transfer_report(tag):
    counts = {"Aa aa": {"presences_total": 10, "presences_held_out": 3,
                        "background_held_out": 5}}
    rt.write_report([], counts, SPP, [FakeLayer(["x"])],
                    {"name": "Agder fylke"},
                    {"name": "Kristiansand kommune (post-2020 merge)"},
                    4000, tag)


class TestBlockedCvReport:
    def test_untagged_writes_the_canonical_file(self, processed):
        model_report(processed, tag=None)
        assert (processed / "model_results.md").exists()

    def test_tagged_writes_only_the_archive(self, processed):
        model_report(processed, tag="no-elevation")
        assert not (processed / "model_results.md").exists()
        assert (processed / "runs" / "model_results_no-elevation.md").exists()

    def test_a_tagged_run_cannot_clobber_an_existing_canonical_file(self, processed):
        canonical = processed / "model_results.md"
        canonical.write_text("the published numbers", encoding="utf-8")
        model_report(processed, tag="no-vol-bounds")
        assert canonical.read_text(encoding="utf-8") == "the published numbers"

    def test_the_national_extent_writes_its_own_file(self, processed, monkeypatch):
        monkeypatch.setattr(rm, "EXTENT", rm.NORGE.key)
        model_report(processed, tag=None)
        assert (processed / "model_results_norge.md").exists()
        assert not (processed / "model_results.md").exists()

    def test_a_national_tagged_run_archives_under_its_own_name(self, processed,
                                                               monkeypatch):
        monkeypatch.setattr(rm, "EXTENT", rm.NORGE.key)
        model_report(processed, tag="no-elevation")
        assert (processed / "runs" / "model_results_norge_no-elevation.md").exists()
        assert not (processed / "runs" / "model_results_no-elevation.md").exists()


class TestTransferReport:
    def test_untagged_writes_the_canonical_file(self, processed):
        transfer_report(tag=None)
        assert (processed / "transferability.md").exists()

    def test_tagged_writes_only_the_archive(self, processed):
        # The bug this file exists for. Before 2026-09-13 this wrote
        # transferability.md too, so comparing a variant against the baseline
        # replaced the baseline with the variant.
        transfer_report(tag="no-vol-bounds")
        assert not (processed / "transferability.md").exists()
        assert (processed / "runs" / "transferability_no-vol-bounds.md").exists()

    def test_a_tagged_run_cannot_clobber_an_existing_canonical_file(self, processed):
        canonical = processed / "transferability.md"
        canonical.write_text("the published numbers", encoding="utf-8")
        transfer_report(tag="no-vol-bounds")
        assert canonical.read_text(encoding="utf-8") == "the published numbers"

    def test_the_national_extent_writes_its_own_file(self, processed, monkeypatch):
        monkeypatch.setattr(rm, "EXTENT", rm.NORGE.key)
        transfer_report(tag=None)
        assert (processed / "transferability_norge.md").exists()
        assert not (processed / "transferability.md").exists()


class TestTheFourCanonicalNamesAreDistinct:
    def test_no_two_reports_share_a_filename(self):
        names = [rm.AGDER.report, rm.NORGE.report,
                 rm.AGDER.transfer_report, rm.NORGE.transfer_report]
        assert len(set(names)) == 4
