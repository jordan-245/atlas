"""
Atlas Cross-Sectional Value + Quality Factor Strategy (Gate-1)
================================================================
A cross-sectional FUNDAMENTAL factor book on the survivorship-correct mid/small-cap universe
(`shm`). Built from POINT-IN-TIME Sharadar SF1 fundamentals (datekey-aligned, +1 trading-day
lag — no look-ahead). This is the one "better data" lever not refuted by the 22 price/technical
nulls: every prior shm strategy used only OHLCV; this uses information orthogonal to the price.

Construction is FROZEN by the pre-registration:
  research/strategies/cross_sectional_value_quality_GATE1_SPEC.md
  - VALUE composite  = mean z of [1/pe, 1/pb, fcf/marketcap]   (cheaper -> higher)
  - QUALITY composite= mean z of [roe, roa, grossmargin, -de]  (better -> higher)
  - COMBINED         = w_value*value_z + (1-w_value)*quality_z  (w_value default 0.5)
  - Monthly rebalance, long top quintile, long-only, sector-tagged.
  - Sizing/stops: Atlas HOUSE model (ATR stop + risk-per-trade), identical to csm and all 22
    comparison strategies (2026-06-08 amendment). Primary exit = monthly rank (leaves top
    quintile); ATR stop is the catastrophic backstop the engine manages. Equal-weight was
    dropped because this engine sizes by risk(stop-distance) and has no notional path —
    matching the house sizing keeps the value-vs-momentum comparison clean and controlled.

Point-in-time integrity: fundamentals are merged onto each trading day with merge_asof
(direction='backward', allow_exact_matches=False) so a filing is only usable STRICTLY AFTER its
datekey. The cross-sectional z-scoring happens at signal time across the universe's latest bars.

Reference: Fama-French (HML), Novy-Marx (gross profitability), Asness-Frazzini-Pedersen (QMJ).
Config Section: strategies.cross_sectional_value_quality
"""
import logging
import warnings
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.helpers import calc_atr, calc_position_size

logger = logging.getLogger(__name__)

_PROJECT = Path(__file__).resolve().parent.parent.parent
_FUND_PARQUET = _PROJECT / "data" / "cache" / "shm_fundamentals.parquet"

_VALUE_COLS = ["_ey", "_bp", "_fy"]
_QUAL_COLS = ["_roe", "_roa", "_gm", "_lev"]
_ALL_COLS = _VALUE_COLS + _QUAL_COLS


@lru_cache(maxsize=2)
def _load_fundamentals(path_str: str, mtime: float) -> Dict[str, pd.DataFrame]:
    """Load SF1 point-in-time fundamentals -> {ticker: frame sorted by datekey with component
    columns}. Cached by (path, mtime) so the battery's many grid configs share one load."""
    df = pd.read_parquet(path_str)
    df = df.copy()
    df["datekey"] = pd.to_datetime(df["datekey"], errors="coerce")
    df = df.dropna(subset=["datekey"])
    pe = pd.to_numeric(df["pe"], errors="coerce")
    pb = pd.to_numeric(df["pb"], errors="coerce")
    mcap = pd.to_numeric(df["marketcap"], errors="coerce")
    fcf = pd.to_numeric(df["fcf"], errors="coerce")
    df["_ey"] = np.where(pe > 0, 1.0 / pe, np.nan)            # earnings yield (1/pe)
    df["_bp"] = np.where(pb > 0, 1.0 / pb, np.nan)            # book-to-price (1/pb)
    df["_fy"] = np.where(mcap > 0, fcf / mcap, np.nan)        # FCF yield
    df["_roe"] = pd.to_numeric(df["roe"], errors="coerce")
    df["_roa"] = pd.to_numeric(df["roa"], errors="coerce")
    df["_gm"] = pd.to_numeric(df["grossmargin"], errors="coerce")
    df["_lev"] = -pd.to_numeric(df["de"], errors="coerce")    # low leverage -> higher quality
    keep = ["ticker", "datekey"] + _ALL_COLS
    out: Dict[str, pd.DataFrame] = {}
    for tkr, g in df[keep].sort_values("datekey").groupby("ticker"):
        out[str(tkr)] = g.reset_index(drop=True)
    return out


def _winsor_z(x: np.ndarray) -> np.ndarray:
    """NaN-aware winsorize (1/99 pct) then z-score across the cross-section."""
    out = np.full(x.shape, np.nan)
    m = np.isfinite(x)
    if m.sum() < 5:
        return out
    v = x[m]
    lo, hi = np.nanpercentile(v, [1, 99])
    v = np.clip(v, lo, hi)
    mu, sd = v.mean(), v.std(ddof=0)
    out[m] = (v - mu) / sd if sd > 0 else 0.0
    return out


class CrossSectionalValueQuality(BaseStrategy):
    """Long-only cross-sectional value+quality fundamental factor book (monthly, equal-weight)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        c = config.get("strategies", {}).get("cross_sectional_value_quality", {})
        self.w_value = float(c.get("w_value", 0.5))          # FROZEN default 0.5/0.5 blend
        self.top_pct = float(c.get("top_pct", 0.20))         # top quintile
        self.min_price = float(c.get("min_price", 5.0))
        # Sizing/stop: Atlas house model (ATR stop + risk-per-trade), IDENTICAL to csm and all 22
        # comparison strategies (2026-06-08 amendment — see GATE1_SPEC). Keeps the test a clean
        # controlled comparison: only the RANKING signal differs (fundamentals vs price).
        self.atr_period = int(c.get("atr_period", 14))
        self.atr_stop_mult = float(c.get("atr_stop_mult", 3.0))
        self.risk_pct = float(config.get("risk", {}).get("max_risk_per_trade_pct", 0.005))
        self.market = config.get("market", "shm")
        self.book_size = int(config.get("risk", {}).get("max_open_positions", 10))
        # sector map (REQUIRED — deployment rail collapses an untagged book)
        self._sectors = dict(_load_sector_map(self.market))
        self._fund = _load_fund_for(self.market)
        self._target_month: Tuple[int, int] = (0, 0)
        self._target_set: set = set()
        self._logger.info(
            "CrossSectionalValueQuality init: w_value=%.2f top_pct=%.2f book=%d sectors=%d funds=%d",
            self.w_value, self.top_pct, self.book_size, len(self._sectors), len(self._fund),
        )

    @property
    def name(self) -> str:
        return "cross_sectional_value_quality"

    # ------------------------------------------------------------------ precompute
    def precompute(self, data: Dict[str, pd.DataFrame]) -> None:
        """Attach point-in-time fundamental component columns to each ticker's OHLCV frame via
        a causal merge_asof (filing usable only STRICTLY after its datekey -> +1 day lag)."""
        for ticker, df in data.items():
            if df is None or df.empty:
                continue
            fund = self._fund.get(ticker)
            if fund is None or fund.empty:
                for col in _ALL_COLS:
                    df[col] = np.nan
                continue
            left = pd.DataFrame({"_d": pd.to_datetime(df.index)})
            merged = pd.merge_asof(
                left, fund, left_on="_d", right_on="datekey",
                direction="backward", allow_exact_matches=False,
            )
            for col in _ALL_COLS:
                df[col] = merged[col].to_numpy()
            # ATR for stop/sizing (Atlas house model)
            if {"high", "low", "close"}.issubset(df.columns):
                df["_vqatr"] = calc_atr(df["high"], df["low"], df["close"], period=self.atr_period)
            else:
                df["_vqatr"] = df["close"].pct_change().rolling(self.atr_period).std() * df["close"]
        self._precomputed = True

    # ------------------------------------------------------------------ ranking
    def _rank_universe(self, data: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
        tickers, comps, prices, atrs = [], [], [], []
        for ticker, df in data.items():
            if df is None or df.empty or "close" not in df.columns:
                continue
            last = df.iloc[-1]
            price = last.get("close")
            if price is None or not np.isfinite(price) or price < self.min_price:
                continue
            row = [float(last[col]) if pd.notna(last.get(col)) else np.nan for col in _ALL_COLS]
            if not np.any(np.isfinite(row)):
                continue
            atr = float(last["_vqatr"]) if ("_vqatr" in df.columns and pd.notna(last.get("_vqatr"))) else np.nan
            tickers.append(ticker)
            comps.append(row)
            prices.append(float(price))
            atrs.append(atr)
        if len(tickers) < 5:
            return {}
        comps = np.array(comps, dtype=float)  # (N, 7)
        z = np.column_stack([_winsor_z(comps[:, i]) for i in range(comps.shape[1])])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows -> NaN (filtered below)
            val = np.nanmean(z[:, 0:3], axis=1)   # value composite
            qual = np.nanmean(z[:, 3:7], axis=1)  # quality composite
        with np.errstate(invalid="ignore"):
            combined = self.w_value * val + (1.0 - self.w_value) * qual
        valid = np.isfinite(combined)  # requires >=1 value AND >=1 quality component
        idxs = np.where(valid)[0]
        order = idxs[np.argsort(-combined[idxs])]
        out: Dict[str, dict] = {}
        for rank, i in enumerate(order, start=1):
            out[tickers[i]] = {"rank": rank, "combined": float(combined[i]),
                               "value": float(val[i]) if np.isfinite(val[i]) else None,
                               "quality": float(qual[i]) if np.isfinite(qual[i]) else None,
                               "price": prices[i], "atr": atrs[i]}
        return out

    def _get_target(self, data: Dict[str, pd.DataFrame]) -> set:
        """Month-cached target set (top quintile capped at book size). Idempotent within a month,
        so entries/exits only move at the monthly rebalance."""
        today = None
        for df in data.values():
            if df is not None and len(df):
                d = df.index[-1]
                if today is None or d > today:
                    today = d
        if today is None:
            return self._target_set
        ym = (today.year, today.month)
        if ym != self._target_month:
            ranks = self._rank_universe(data)
            if ranks:
                cut = max(1, int(self.top_pct * len(ranks)))
                cut = min(cut, self.book_size)
                ordered = sorted(ranks.items(), key=lambda kv: kv[1]["rank"])[:cut]
                self._target_set = {t for t, _ in ordered}
                self._target_month = ym
        return self._target_set

    # ------------------------------------------------------------------ entries
    def generate_signals(self, data: Dict[str, pd.DataFrame], equity: float,
                         existing_positions: List[Dict[str, Any]]) -> List[Signal]:
        signals: List[Signal] = []
        target = self._get_target(data)
        if not target:
            return signals
        ranks = self._rank_universe(data)
        held = self._get_held_tickers(existing_positions)
        commission = self.fees_config.get("commission_per_trade", 0.0)
        commission_pct = self.fees_config.get("commission_pct", 0.0)
        for ticker in sorted(target, key=lambda t: ranks.get(t, {}).get("rank", 1e9)):
            if ticker in held:
                continue
            if not self._can_open_position(existing_positions):
                break
            info = ranks.get(ticker)
            if info is None:
                continue
            price = info["price"]; atr = info["atr"]
            if not np.isfinite(atr) or atr <= 0:
                continue
            stop = price - self.atr_stop_mult * atr
            if stop <= 0 or stop >= price:
                continue
            pos = calc_position_size(equity=equity, risk_pct=self.risk_pct, entry_price=price,
                                     stop_price=stop, commission_per_trade=commission,
                                     commission_pct=commission_pct)
            if pos["shares"] <= 0:
                continue
            sector = self._sectors.get(ticker, "Unknown")
            signals.append(Signal(
                ticker=ticker, strategy=self.name, direction="long",
                entry_price=price, stop_price=round(stop, 4), take_profit=None,
                position_size=pos["shares"], position_value=pos["position_value"],
                risk_amount=pos["total_risk"],
                confidence=0.7,
                rationale=(f"{ticker} rank {info['rank']} in top-{int(self.top_pct*100)}% "
                           f"value+quality (combined z {info['combined']:.2f})."),
                sector=sector,
                features={"rank": info["rank"], "combined": round(info["combined"], 3),
                          "value_z": info["value"], "quality_z": info["quality"],
                          "sector": sector},
                timestamp=datetime.now(),
            ))
        self._logger.info("%s: %d entry signals (target=%d, ranked=%d)",
                          self.name, len(signals), len(target), len(ranks))
        return signals

    # ------------------------------------------------------------------ exits
    def check_exits(self, data: Dict[str, pd.DataFrame],
                    positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        exits: List[Dict[str, Any]] = []
        target = self._get_target(data)
        for ticker, pos, df in self._iter_my_positions(data, positions):
            if df is None or df.empty:
                continue
            price = float(df["close"].iloc[-1])
            if ticker not in target:
                exits.append({"ticker": ticker, "reason": "signal_exit", "exit_price": price,
                              "details": "left top quintile (monthly rebalance)"})
        return exits


# ---- sector map loader (mirrors cross_sectional_momentum precedence) --------------------
@lru_cache(maxsize=4)
def _load_sector_map(market: str) -> tuple:
    import json
    for p in (_PROJECT / "data" / "processed" / f"sector_map_{market}.json",
              _PROJECT / "data" / "processed" / "sector_map.json"):
        if p.exists():
            try:
                m = json.load(open(p))
                if m:
                    return tuple((k, v) for k, v in m.items() if v)
            except Exception as e:
                logger.warning("cross_sectional_value_quality: sector map load failed %s: %s", p, e)
    logger.warning("cross_sectional_value_quality: no sector map for market=%s", market)
    return tuple()


def _load_fund_for(market: str) -> Dict[str, pd.DataFrame]:
    if not _FUND_PARQUET.exists():
        logger.error("cross_sectional_value_quality: fundamentals parquet missing: %s", _FUND_PARQUET)
        return {}
    return _load_fundamentals(str(_FUND_PARQUET), _FUND_PARQUET.stat().st_mtime)


# Default parameter grid for the cross-OOS battery (honest search-burden accounting for DSR).
# Primary (--select default) = the FROZEN pre-registered config: w_value 0.5, top_pct 0.20, min_price 5.
PARAM_GRID = {
    "w_value": [0.3, 0.5, 0.7],
    "top_pct": [0.10, 0.20],
    "atr_stop_mult": [2.5, 3.0, 3.5],
}
