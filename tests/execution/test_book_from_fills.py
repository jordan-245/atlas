"""book-from-fills: the virtual book is updated from RECONCILED ACTUAL fills (record_fills), never on
order acceptance. Prevents the phantom/qty-drift class that corrupted the books (2026-06-16 fix)."""
import json

import pytest

from atlas.execution import record_fills as rf


class _FakeStatus:
    def __init__(self, status, fill_price, filled_qty):
        self.status = status
        self.fill_price = fill_price
        self.filled_qty = filled_qty


class _FakeBroker:
    def __init__(self, by_oid):
        self.by_oid = by_oid

    def get_order_status(self, oid):
        return self.by_oid[oid]


@pytest.fixture()
def live(tmp_path, monkeypatch):
    import atlas.execution.virtual_book as vb
    monkeypatch.setattr(rf, "LIVE_DATA", tmp_path)
    monkeypatch.setattr(vb, "LIVE_DATA", tmp_path)
    monkeypatch.setattr(rf, "_fetch_open_map", lambda *a, **k: {})   # no market-data call
    return tmp_path


def _setup(live, name, orders, cap=10000.0):
    d = live / name
    d.mkdir(parents=True)
    (d / "book.json").write_text(json.dumps({"cash": cap, "positions": {}, "capital_base": cap}))
    (d / "runs.jsonl").write_text(json.dumps(
        {"date": "2026-06-16", "dry_run": False, "blocked": False, "orders": orders}) + "\n")
    return d


def _book(live, name):
    return json.loads((live / name / "book.json").read_text())


def test_books_only_actual_fills_not_acceptance(live):
    # o1 fills; o2 is an OPG short that never fills (the phantom case) — it must NOT be booked
    _setup(live, "s1", [
        {"ticker": "AAA", "side": "BUY", "qty": 10, "px": 50.0, "order_id": "o1"},
        {"ticker": "BBB", "side": "SELL", "qty": 5, "px": 20.0, "order_id": "o2"},
    ])
    broker = _FakeBroker({"o1": _FakeStatus("filled", 50.2, 10),
                          "o2": _FakeStatus("canceled", 0.0, 0)})
    assert rf.reconcile_book("s1", broker) == 2
    book = _book(live, "s1")
    assert book["positions"] == {"AAA": 10}                       # unfilled short NOT a phantom
    assert round(book["cash"], 2) == round(10000.0 - 10 * 50.2, 2)


def test_partial_fill_books_only_filled_qty(live):
    _setup(live, "s2", [{"ticker": "AAA", "side": "BUY", "qty": 10, "px": 50.0, "order_id": "o1"}])
    rf.reconcile_book("s2", _FakeBroker({"o1": _FakeStatus("partially_filled", 50.0, 4)}))
    assert _book(live, "s2")["positions"] == {"AAA": 4}           # actual 4, not requested 10


def test_idempotent_no_double_apply(live):
    _setup(live, "s3", [{"ticker": "AAA", "side": "BUY", "qty": 10, "px": 50.0, "order_id": "o1"}])
    broker = _FakeBroker({"o1": _FakeStatus("filled", 50.0, 10)})
    rf.reconcile_book("s3", broker)
    assert rf.reconcile_book("s3", broker) == 0                   # o1 already reconciled
    assert _book(live, "s3")["positions"] == {"AAA": 10}          # not 20


def test_short_fill_books_negative(live):
    _setup(live, "s4", [{"ticker": "SH", "side": "SELL", "qty": 7, "px": 30.0, "order_id": "o1"}])
    rf.reconcile_book("s4", _FakeBroker({"o1": _FakeStatus("filled", 30.0, 7)}))
    book = _book(live, "s4")
    assert book["positions"] == {"SH": -7}
    assert round(book["cash"], 2) == round(10000.0 + 7 * 30.0, 2)  # short adds cash
