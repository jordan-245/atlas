"""Tests for the book↔broker invariant guard (atlas/execution/reconcile_books.py)."""
from atlas.execution.reconcile_books import compare


def test_clean_when_books_sum_to_broker():
    broker = {"AAA": 10, "BBB": -5}
    books = {"s1": {"AAA": 10}, "s2": {"BBB": -5}}
    rep = compare(broker, books)
    assert rep["ok"] and rep["n_orphans"] == 0 and rep["n_phantoms"] == 0 and rep["n_mismatch"] == 0


def test_aggregates_across_books_for_shared_ticker():
    # a ticker held by two strategies sums; matches a larger broker position
    broker = {"AAA": 15}
    books = {"s1": {"AAA": 10}, "s2": {"AAA": 5}}
    assert compare(broker, books)["ok"]


def test_orphan_broker_holds_no_book_claims():
    broker = {"AAA": 10, "ORPH": 3}
    books = {"s1": {"AAA": 10}}
    rep = compare(broker, books)
    assert not rep["ok"]
    assert rep["orphans"] == {"ORPH": 3} and rep["n_orphans"] == 1
    assert rep["n_phantoms"] == 0 and rep["n_mismatch"] == 0


def test_phantom_book_claims_broker_missing():
    # the OPG-short-that-never-filled case: a book records a short the broker doesn't hold
    broker = {"AAA": 10}
    books = {"s1": {"AAA": 10}, "s2": {"PHAN": -7}}
    rep = compare(broker, books)
    assert not rep["ok"]
    assert rep["phantoms"] == {"PHAN": -7} and rep["n_phantoms"] == 1


def test_qty_mismatch_flagged_with_both_sides():
    broker = {"AAA": 40}
    books = {"s1": {"AAA": 17}}
    rep = compare(broker, books)
    assert not rep["ok"]
    assert rep["mismatch"] == {"AAA": {"broker": 40, "books": 17}}


def test_zero_quantities_ignored_both_sides():
    broker = {"AAA": 0, "BBB": 10}
    books = {"s1": {"BBB": 10, "CCC": 0}}
    assert compare(broker, books)["ok"]


def test_empty_both_is_clean():
    assert compare({}, {})["ok"]


def test_flat_broker_with_phantom_books_is_drift():
    # the mid-reset state: broker flattened, a book still claims positions
    rep = compare({}, {"s1": {"AAA": 5}})
    assert not rep["ok"] and rep["n_phantoms"] == 1
