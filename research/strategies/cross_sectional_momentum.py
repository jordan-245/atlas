"""
Atlas Cross-Sectional Momentum + Quality Strategy
===================================================
A cross-sectional FACTOR book (not a single-name timing strategy). Each day it ranks the
entire tradable universe by a composite of:
  - MOMENTUM   : 12-1 month total return (close[t-skip] / close[t-lookback] - 1), the classic
                 Jegadeesh-Titman signal (skip the most recent month to avoid short reversal).
  - QUALITY    : low realised volatility of daily returns (Frazzini-Pedersen low-vol/quality
                 proxy on price-only data) -> lower vol scores higher.
Scores are cross-sectionally z-scored across the universe each day and combined. We go LONG
the top-ranked names that are also above their long-term trend (SMA filter). Exits are
rank-based with hysteresis (drop out of the top `exit_rank`), plus an ATR hard stop, a trend
break, and a max-hold cap.

Why this design (board memo 2026-06-03): breadth across ~200 names = many more independent
bets than a single-name breakout -> higher *achievable* Deflated Sharpe under the cross-OOS
battery, and it scales with AUM. Uses existing daily OHLCV only (no new data).

Reference: Jegadeesh & Titman (1993); Frazzini & Pedersen (2014).
Config Section: strategies.cross_sectional_momentum
"""
import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.helpers import calc_atr, calc_position_size

logger = logging.getLogger(__name__)

_PROJECT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=4)
def _load_sector_map(market: str) -> tuple:
    """Load the per-market ticker->sector map (matches scripts/cli.py precedence).

    Returns a tuple of (ticker, sector) pairs (hashable for lru_cache); callers dict() it.
    Without sectors, the engine's max_sector_concentration cap collapses the whole book to
    one 'Unknown' bucket (capped at 2) — so a cross-sectional book MUST tag real sectors.
    """
    for p in (_PROJECT / "data" / "processed" / f"sector_map_{market}.json",
              _PROJECT / "data" / "processed" / "sector_map.json"):
        if p.exists():
            try:
                m = json.load(open(p))
                if m:
                    return tuple((k, v) for k, v in m.items() if v)
            except Exception as e:
                logger.warning("cross_sectional_momentum: sector map load failed for %s: %s", p, e)
    logger.warning("cross_sectional_momentum: no sector map for market=%s — sector cap will collapse book", market)
    return tuple()


class CrossSectionalMomentum(BaseStrategy):
    """Cross-sectional momentum + low-vol (quality) factor book, long-only."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        c = config.get("strategies", {}).get("cross_sectional_momentum", {})
        # Defaults reflect the first research iteration (2026-06-03): 6-month momentum +
        # partial low-vol + breadth-30 beat the 12-month/qual=1 baseline materially
        # (CPCV median 0.72 vs 0.06, gross Sharpe 0.75, PF 1.62, MaxDD 7.2%). The full
        # cross-OOS battery (with effective-N DSR) is the authoritative judge — these are a
        # principled starting point (6-1 momentum is a standard equities horizon), not a
        # tuned-to-death config.
        self.mom_lookback = int(c.get("mom_lookback", 126))   # ~6 months
        self.mom_skip = int(c.get("mom_skip", 21))            # skip last ~1 month
        self.vol_lookback = int(c.get("vol_lookback", 126))   # ~6 months
        self.sma_period = int(c.get("sma_period", 200))
        self.atr_period = int(c.get("atr_period", 14))
        self.atr_stop_mult = float(c.get("atr_stop_mult", 3.0))
        self.top_n = int(c.get("top_n", 30))                  # entry rank
        self.exit_rank = int(c.get("exit_rank", 60))          # hysteresis exit rank
        self.max_hold_days = int(c.get("max_hold_days", 90))
        self.w_mom = float(c.get("w_mom", 1.0))
        self.w_qual = float(c.get("w_qual", 0.5))
        self.trend_filter = bool(c.get("trend_filter", True))
        self.min_price = float(c.get("min_price", 5.0))
        self.market = config.get("market", "sp500")
        self._sectors = dict(_load_sector_map(self.market))
        self._logger.info(
            "CrossSectionalMomentum init: mom=%d-%d vol=%d top_n=%d exit_rank=%d sectors=%d",
            self.mom_lookback, self.mom_skip, self.vol_lookback, self.top_n, self.exit_rank,
            len(self._sectors),
        )

    @property
    def name(self) -> str:
        return "cross_sectional_momentum"

    # ------------------------------------------------------------------ precompute
    def precompute(self, data: Dict[str, pd.DataFrame]) -> None:
        """Add factor columns once so per-day ranking is O(universe) cheap."""
        for _ticker, df in data.items():
            if df is None or df.empty or "close" not in df.columns:
                continue
            close = df["close"]
            # 12-1 momentum: close skipped-back / close lookback-back - 1
            df["_csm_mom"] = close.shift(self.mom_skip) / close.shift(self.mom_lookback) - 1.0
            ret = close.pct_change()
            df["_csm_vol"] = ret.rolling(self.vol_lookback,
                                         min_periods=max(20, self.vol_lookback // 2)).std()
            df["_csm_sma"] = close.rolling(self.sma_period,
                                           min_periods=self.sma_period).mean()
            if {"high", "low"}.issubset(df.columns):
                df["_csm_atr"] = calc_atr(df["high"], df["low"], close, period=self.atr_period)
            else:
                df["_csm_atr"] = ret.rolling(self.atr_period).std() * close
        self._precomputed = True

    # ------------------------------------------------------------------ ranking
    def _factor_row(self, df: pd.DataFrame):
        """(mom, vol, sma, atr, price) at the latest bar. Uses precomputed columns when
        present (engine fast-path), else computes directly from the OHLCV tail (screen path).
        Returns None if data is insufficient."""
        n = len(df)
        if n < self.mom_lookback + 2:
            return None
        last = df.iloc[-1]
        price = last.get("close")
        if price is None or not np.isfinite(price):
            return None
        if "_csm_mom" in df.columns and pd.notna(last.get("_csm_mom")) and pd.notna(last.get("_csm_vol")):
            mom = float(last["_csm_mom"]); vol = float(last["_csm_vol"])
            sma = float(last["_csm_sma"]) if pd.notna(last.get("_csm_sma")) else np.nan
            atr = float(last["_csm_atr"]) if pd.notna(last.get("_csm_atr")) else np.nan
            return mom, vol, sma, atr, float(price)
        # Fallback: compute from the tail directly (no precompute dependency).
        c = df["close"].to_numpy(dtype=float)
        mom = c[-1 - self.mom_skip] / c[-1 - self.mom_lookback] - 1.0
        seg = c[-(self.vol_lookback + 1):]
        rets = np.diff(seg) / seg[:-1]
        vol = float(np.std(rets, ddof=0)) if rets.size >= 20 else np.nan
        sma = float(c[-self.sma_period:].mean()) if n >= self.sma_period else np.nan
        if {"high", "low"}.issubset(df.columns):
            atr = float(calc_atr(df["high"], df["low"], df["close"], period=self.atr_period).iloc[-1])
        else:
            atr = (vol * float(price)) if np.isfinite(vol) else np.nan
        return float(mom), vol, sma, atr, float(price)

    def _rank_universe(self, data: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
        """Cross-sectional composite score + rank for every eligible ticker at the latest bar."""
        rows = []
        for ticker, df in data.items():
            if df is None or df.empty:
                continue
            fr = self._factor_row(df)
            if fr is None:
                continue
            mom, vol, sma, atr, price = fr
            if price < self.min_price:
                continue
            if not np.isfinite(mom) or not np.isfinite(vol) or vol <= 0:
                continue
            rows.append((ticker, mom, vol, sma, atr, price))
        if len(rows) < 5:
            return {}
        tickers = [r[0] for r in rows]
        mom = np.array([r[1] for r in rows])
        vol = np.array([r[2] for r in rows])

        def _z(x):
            sd = x.std(ddof=0)
            return (x - x.mean()) / sd if sd > 0 else np.zeros_like(x)

        composite = self.w_mom * _z(mom) - self.w_qual * _z(vol)  # low vol -> higher score
        order = np.argsort(-composite)  # descending
        out: Dict[str, dict] = {}
        for rank, idx in enumerate(order, start=1):
            t = tickers[idx]
            _, m, v, sma, atr, price = rows[idx]
            out[t] = {"rank": rank, "composite": float(composite[idx]), "mom": m, "vol": v,
                      "sma": sma, "atr": atr, "price": price,
                      "above_trend": (not self.trend_filter) or (np.isfinite(sma) and price > sma)}
        return out

    # ------------------------------------------------------------------ entries
    def generate_signals(self, data: Dict[str, pd.DataFrame], equity: float,
                         existing_positions: List[Dict[str, Any]]) -> List[Signal]:
        signals: List[Signal] = []
        ranks = self._rank_universe(data)
        if not ranks:
            return signals
        held = self._get_held_tickers(existing_positions)
        risk_pct = self.risk_config.get("max_risk_per_trade_pct", 0.005)
        commission = self.fees_config.get("commission_per_trade", 0.0)
        commission_pct = self.fees_config.get("commission_pct", 0.0)

        # Candidates = top_n by composite, above trend, not already held.
        candidates = sorted(ranks.items(), key=lambda kv: kv[1]["rank"])
        for ticker, info in candidates:
            if info["rank"] > self.top_n:
                break
            if ticker in held or not info["above_trend"]:
                continue
            if not self._can_open_position(existing_positions):
                break
            atr = info["atr"]; price = info["price"]
            if not np.isfinite(atr) or atr <= 0:
                continue
            stop_price = price - self.atr_stop_mult * atr
            if stop_price <= 0 or stop_price >= price:
                continue
            pos = calc_position_size(equity=equity, risk_pct=risk_pct, entry_price=price,
                                     stop_price=stop_price, commission_per_trade=commission,
                                     commission_pct=commission_pct)
            if pos["shares"] <= 0:
                continue
            signals.append(Signal(
                ticker=ticker, strategy=self.name, direction="long",
                entry_price=price, stop_price=round(stop_price, 4), take_profit=None,
                position_size=pos["shares"], position_value=pos["position_value"],
                risk_amount=pos["total_risk"],
                confidence=float(min(0.9, 0.5 + 0.4 * (self.top_n - info["rank"] + 1) / self.top_n)),
                rationale=(f"{ticker} rank {info['rank']}/{self.top_n}: 12-1 mom "
                           f"{info['mom']*100:.1f}%, vol {info['vol']*100:.2f}%/day, above trend."),
                sector=self._sectors.get(ticker, "Unknown"),
                features={"rank": info["rank"], "composite": round(info["composite"], 3),
                          "mom": round(info["mom"], 4), "vol": round(info["vol"], 5),
                          "sector": self._sectors.get(ticker, "Unknown")},
                timestamp=datetime.now(),
            ))
        self._logger.info("%s: %d entry signals (universe ranked: %d)",
                          self.name, len(signals), len(ranks))
        return signals

    # ------------------------------------------------------------------ exits
    def check_exits(self, data: Dict[str, pd.DataFrame],
                    positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        exits: List[Dict[str, Any]] = []
        ranks = self._rank_universe(data)
        for ticker, pos, df in self._iter_my_positions(data, positions):
            price = float(df["close"].iloc[-1])
            stop_price = pos.get("stop_price", 0) or 0
            entry_date = pd.Timestamp(pos["entry_date"])
            days_held = (df.index[-1] - entry_date).days

            # 1. Hard ATR stop
            if stop_price and price <= stop_price:
                exits.append({"ticker": ticker, "reason": "stop_hit", "exit_price": price,
                              "details": f"Price {price:.2f} <= stop {stop_price:.2f}"})
                continue
            # 2. Trend break
            info = ranks.get(ticker)
            if self.trend_filter and info is not None and np.isfinite(info["sma"]) and price < info["sma"]:
                exits.append({"ticker": ticker, "reason": "signal_exit", "exit_price": price,
                              "details": f"Price {price:.2f} below trend SMA {info['sma']:.2f}"})
                continue
            # 3. Rank-based hysteresis exit (dropped out of the top exit_rank)
            if info is None or info["rank"] > self.exit_rank:
                rk = info["rank"] if info else "n/a"
                exits.append({"ticker": ticker, "reason": "signal_exit", "exit_price": price,
                              "details": f"Rank {rk} fell out of top {self.exit_rank}"})
                continue
            # 4. Max hold
            if days_held >= self.max_hold_days:
                exits.append({"ticker": ticker, "reason": "time_exit", "exit_price": price,
                              "details": f"Held {days_held}d >= max {self.max_hold_days}"})
        return exits


# Default parameter grid for the autoresearch sweeper
PARAM_GRID = {
    "mom_lookback": [126, 189, 252],
    "vol_lookback": [63, 126],
    "atr_stop_mult": [2.5, 3.0, 3.5],
    "top_n": [10, 15, 20, 30],
    "exit_rank": [30, 40, 60],
    "max_hold_days": [60, 90, 120],
    "w_qual": [0.0, 0.5, 1.0],
}
