"""
Atlas Cross-Sectional Value + Momentum + Quality Factor ("Everywhere" composite, Gate-1b)
==========================================================================================
Combines the THREE signals we have now independently measured on the survivorship-correct
mid/small-cap universe (`shm`), each real-but-individually-weak under the rails:
  - VALUE   (SF1 PIT): mean z of [1/pe, 1/pb, fcf/marketcap]
  - QUALITY (SF1 PIT): mean z of [roe, roa, grossmargin, -de]
  - MOMENTUM (price)  : z of 12-1 month return (close[t-21]/close[t-126]-1)

Rationale (USES the learnings, not a re-roll): value & momentum are the canonical COMPLEMENTARY
pair (Asness-Moskowitz-Pedersen 2013, "Value and Momentum Everywhere"). Measured orthogonality
on OUR search window (2017-2024, 91 monthly snapshots): corr(mom,value)=-0.195, corr(mom,qual)
=+0.02, corr(value,qual)=-0.094 -> genuinely orthogonal, so combining diversifies signal noise.
Combining orthogonal weak factors is the textbook robustness move; this is a NEW hypothesis family.

Construction FROZEN (pre-reg: cross_sectional_value_momentum_GATE1b_SPEC.md). House ATR-stop risk
sizing (identical to csm + value_quality), monthly rebalance, long top quintile, sector-tagged.
Point-in-time: fundamentals merged with merge_asof (strictly after datekey, +1 day lag).
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
    df = pd.read_parquet(path_str).copy()
    df["datekey"] = pd.to_datetime(df["datekey"], errors="coerce")
    df = df.dropna(subset=["datekey"])
    pe = pd.to_numeric(df["pe"], errors="coerce"); pb = pd.to_numeric(df["pb"], errors="coerce")
    mcap = pd.to_numeric(df["marketcap"], errors="coerce"); fcf = pd.to_numeric(df["fcf"], errors="coerce")
    df["_ey"] = np.where(pe > 0, 1.0 / pe, np.nan)
    df["_bp"] = np.where(pb > 0, 1.0 / pb, np.nan)
    df["_fy"] = np.where(mcap > 0, fcf / mcap, np.nan)
    df["_roe"] = pd.to_numeric(df["roe"], errors="coerce")
    df["_roa"] = pd.to_numeric(df["roa"], errors="coerce")
    df["_gm"] = pd.to_numeric(df["grossmargin"], errors="coerce")
    df["_lev"] = -pd.to_numeric(df["de"], errors="coerce")
    keep = ["ticker", "datekey"] + _ALL_COLS
    out: Dict[str, pd.DataFrame] = {}
    for tkr, g in df[keep].sort_values("datekey").groupby("ticker"):
        out[str(tkr)] = g.reset_index(drop=True)
    return out


def _winsor_z(x: np.ndarray) -> np.ndarray:
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


class CrossSectionalValueMomentum(BaseStrategy):
    """Long-only combined value+momentum+quality cross-sectional factor book (monthly)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        c = config.get("strategies", {}).get("cross_sectional_value_momentum", {})
        # FROZEN default: w_mom ~ 1/3 (momentum), remaining 2/3 split equally value/quality
        self.w_mom = float(c.get("w_mom", 0.34))
        self.top_pct = float(c.get("top_pct", 0.20))
        self.min_price = float(c.get("min_price", 5.0))
        self.mom_lookback = int(c.get("mom_lookback", 126))
        self.mom_skip = int(c.get("mom_skip", 21))
        self.atr_period = int(c.get("atr_period", 14))
        self.atr_stop_mult = float(c.get("atr_stop_mult", 3.0))
        self.risk_pct = float(config.get("risk", {}).get("max_risk_per_trade_pct", 0.005))
        self.market = config.get("market", "shm")
        self.book_size = int(config.get("risk", {}).get("max_open_positions", 10))
        self._sectors = dict(_load_sector_map(self.market))
        self._fund = _load_fund_for(self.market)
        self._target_month: Tuple[int, int] = (0, 0)
        self._target_set: set = set()
        self._logger.info(
            "CrossSectionalValueMomentum init: w_mom=%.2f top_pct=%.2f book=%d sectors=%d funds=%d",
            self.w_mom, self.top_pct, self.book_size, len(self._sectors), len(self._fund),
        )

    @property
    def name(self) -> str:
        return "cross_sectional_value_momentum"

    def precompute(self, data: Dict[str, pd.DataFrame]) -> None:
        for ticker, df in data.items():
            if df is None or df.empty:
                continue
            fund = self._fund.get(ticker)
            if fund is None or fund.empty:
                for col in _ALL_COLS:
                    df[col] = np.nan
            else:
                left = pd.DataFrame({"_d": pd.to_datetime(df.index)})
                merged = pd.merge_asof(left, fund, left_on="_d", right_on="datekey",
                                       direction="backward", allow_exact_matches=False)
                for col in _ALL_COLS:
                    df[col] = merged[col].to_numpy()
            close = df["close"] if "close" in df.columns else None
            if close is not None:
                df["_vqmom"] = close.shift(self.mom_skip) / close.shift(self.mom_lookback) - 1.0
                if {"high", "low"}.issubset(df.columns):
                    df["_vqatr"] = calc_atr(df["high"], df["low"], close, period=self.atr_period)
                else:
                    df["_vqatr"] = close.pct_change().rolling(self.atr_period).std() * close
        self._precomputed = True

    def _rank_universe(self, data: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
        tickers, comps, moms, prices, atrs = [], [], [], [], []
        for ticker, df in data.items():
            if df is None or df.empty or "close" not in df.columns:
                continue
            last = df.iloc[-1]
            price = last.get("close")
            if price is None or not np.isfinite(price) or price < self.min_price:
                continue
            row = [float(last[col]) if pd.notna(last.get(col)) else np.nan for col in _ALL_COLS]
            mom = float(last["_vqmom"]) if ("_vqmom" in df.columns and pd.notna(last.get("_vqmom"))) else np.nan
            # require at least one fundamental component AND a momentum value
            if not np.any(np.isfinite(row)) or not np.isfinite(mom):
                continue
            atr = float(last["_vqatr"]) if ("_vqatr" in df.columns and pd.notna(last.get("_vqatr"))) else np.nan
            tickers.append(ticker); comps.append(row); moms.append(mom)
            prices.append(float(price)); atrs.append(atr)
        if len(tickers) < 5:
            return {}
        comps = np.array(comps, dtype=float)
        z = np.column_stack([_winsor_z(comps[:, i]) for i in range(comps.shape[1])])
        mom_z = _winsor_z(np.array(moms, dtype=float))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            val = np.nanmean(z[:, 0:3], axis=1)
            qual = np.nanmean(z[:, 3:7], axis=1)
            fund_z = np.nanmean(np.column_stack([val, qual]), axis=1)  # value+quality blend
        w_rem = 1.0 - self.w_mom
        with np.errstate(invalid="ignore"):
            combined = self.w_mom * mom_z + w_rem * fund_z
        valid = np.isfinite(combined)  # needs momentum AND >=1 fundamental composite
        idxs = np.where(valid)[0]
        order = idxs[np.argsort(-combined[idxs])]
        out: Dict[str, dict] = {}
        for rank, i in enumerate(order, start=1):
            out[tickers[i]] = {"rank": rank, "combined": float(combined[i]),
                               "mom_z": float(mom_z[i]) if np.isfinite(mom_z[i]) else None,
                               "fund_z": float(fund_z[i]) if np.isfinite(fund_z[i]) else None,
                               "price": prices[i], "atr": atrs[i]}
        return out

    def _get_target(self, data: Dict[str, pd.DataFrame]) -> set:
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
                cut = min(max(1, int(self.top_pct * len(ranks))), self.book_size)
                ordered = sorted(ranks.items(), key=lambda kv: kv[1]["rank"])[:cut]
                self._target_set = {t for t, _ in ordered}
                self._target_month = ym
        return self._target_set

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
                risk_amount=pos["total_risk"], confidence=0.7,
                rationale=(f"{ticker} rank {info['rank']} top-{int(self.top_pct*100)}% "
                           f"value+mom+quality (z {info['combined']:.2f})."),
                sector=sector,
                features={"rank": info["rank"], "combined": round(info["combined"], 3),
                          "mom_z": info["mom_z"], "fund_z": info["fund_z"], "sector": sector},
                timestamp=datetime.now(),
            ))
        self._logger.info("%s: %d entry signals (target=%d, ranked=%d)",
                          self.name, len(signals), len(target), len(ranks))
        return signals

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
                logger.warning("cross_sectional_value_momentum: sector map load failed %s: %s", p, e)
    logger.warning("cross_sectional_value_momentum: no sector map for market=%s", market)
    return tuple()


def _load_fund_for(market: str) -> Dict[str, pd.DataFrame]:
    if not _FUND_PARQUET.exists():
        logger.error("cross_sectional_value_momentum: fundamentals parquet missing: %s", _FUND_PARQUET)
        return {}
    return _load_fundamentals(str(_FUND_PARQUET), _FUND_PARQUET.stat().st_mtime)


# Cross-OOS battery grid (honest search-burden for DSR). Primary (--select default) = FROZEN:
# w_mom 0.34 (~1/3 each leg), top_pct 0.20, atr_stop 3.0.
PARAM_GRID = {
    "w_mom": [0.25, 0.34, 0.50],
    "top_pct": [0.10, 0.20],
    "atr_stop_mult": [2.5, 3.0],
}
