"""Process-time second look at a page: merge in RAM, nothing persisted.

Our real accuracy gap is MISSED rows on a dense/faint scan (50 of 52). A second
pass with different pre-processing can recover some; these tests lock the merge
semantics (no duplicates, no losses, first pass wins) and the cost gating."""

import numpy as np
import pytest

from core import ocr_table


def test_merge_adds_only_missed_rows():
    first = [{"account_number": "3577864748", "name": "A"},
             {"account_number": "3577864749", "name": "B"}]
    second = [{"account_number": "3577864748", "name": "A-WORSE"},   # duplicate
              {"account_number": "3577864750", "name": "C"}]         # missed row
    merged, added = ocr_table.merge_row_sets(first, second)
    assert added == 1
    assert len(merged) == 3
    # the FIRST pass's version of a shared row is kept (it saw the clean image)
    assert merged[0]["name"] == "A"
    assert merged[-1]["account_number"] == "3577864750"


def test_merge_dedups_on_digits_only():
    """OCR noise in the account number must not create a phantom duplicate."""
    merged, added = ocr_table.merge_row_sets(
        [{"account_number": "3577 864748", "name": "A"}],
        [{"account_number": "3577864748", "name": "A"}])
    assert added == 0 and len(merged) == 1


def test_merge_falls_back_to_name_mobile_without_account():
    merged, added = ocr_table.merge_row_sets(
        [{"name": "RAMESH", "mobile": "9876543210"}],
        [{"name": "RAMESH", "mobile": "9876543210"},
         {"name": "SITA", "mobile": "9876500000"}])
    assert added == 1 and len(merged) == 2


def test_merge_skips_completely_empty_rows():
    merged, added = ocr_table.merge_row_sets([], [{"name": "", "mobile": ""}])
    assert added == 0 and merged == []


def test_second_pass_off_by_default_does_one_pass(monkeypatch):
    """Cost guard: a second pass roughly doubles per-page CPU time, so it must
    not happen unless explicitly enabled."""
    import config
    monkeypatch.setattr(config, "OCR_SECOND_PASS", "off", raising=False)
    calls = {"n": 0}

    def _fake(pil, angle=None, on_row=None):
        calls["n"] += 1
        return pil, [{"account_number": "1", "name": "A"}], 0

    monkeypatch.setattr(ocr_table, "extract_with_image", _fake)
    out = ocr_table.extract_rows_adaptive(object())
    assert calls["n"] == 1 and out["passes"] == 1 and out["added"] == 0


def test_second_pass_always_merges_extra_rows(monkeypatch):
    import config
    from PIL import Image
    monkeypatch.setattr(config, "OCR_SECOND_PASS", "always", raising=False)
    seq = [[{"account_number": "1", "name": "A"}],
           [{"account_number": "1", "name": "A"}, {"account_number": "2", "name": "B"}]]
    calls = {"n": 0}

    def _fake(pil, angle=None, on_row=None):
        rows = seq[min(calls["n"], 1)]
        calls["n"] += 1
        return Image.new("L", (60, 60), 255), rows, 0

    monkeypatch.setattr(ocr_table, "extract_with_image", _fake)
    out = ocr_table.extract_rows_adaptive(Image.new("L", (60, 60), 255))
    assert calls["n"] == 2
    assert out["passes"] == 2 and out["added"] == 1
    assert len(out["rows"]) == 2


def test_second_pass_failure_keeps_first_pass(monkeypatch):
    """If the second look blows up, we must still return pass 1 — never lose it."""
    import config
    from PIL import Image
    monkeypatch.setattr(config, "OCR_SECOND_PASS", "always", raising=False)
    calls = {"n": 0}

    def _fake(pil, angle=None, on_row=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("second pass exploded")
        return Image.new("L", (60, 60), 255), [{"account_number": "1", "name": "A"}], 0

    monkeypatch.setattr(ocr_table, "extract_with_image", _fake)
    out = ocr_table.extract_rows_adaptive(Image.new("L", (60, 60), 255))
    assert out["passes"] == 1 and len(out["rows"]) == 1


# ── Heavy model weights: download only where explicitly allowed ───────────────

def test_heavy_weights_refused_without_download_permission(monkeypatch, tmp_path):
    """A CSP box must never start fetching ~190 MB of model weights on its own —
    it refuses with an actionable message instead."""
    pytest.importorskip("onnxtr")
    import config
    monkeypatch.setattr(config, "OCR_ALLOW_MODEL_DOWNLOAD", "0", raising=False)
    monkeypatch.setattr(config, "ONNXTR_DET_HEAVY_PATH", str(tmp_path / "nope_det.onnx"),
                        raising=False)
    monkeypatch.setattr(config, "ONNXTR_RECO_HEAVY_PATH", str(tmp_path / "nope_reco.onnx"),
                        raising=False)
    with pytest.raises(FileNotFoundError) as ei:
        ocr_table._build_heavy_onnxtr(lambda **k: None)
    assert "OCR_ALLOW_MODEL_DOWNLOAD" in str(ei.value)
