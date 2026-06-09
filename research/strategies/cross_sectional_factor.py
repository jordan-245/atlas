"""Cross-sectional multi-FACTOR book (Pass-2 search on survivorship-correct mid/small-cap).

Same proven deployment machinery as cross_sectional_momentum (sector-tagged, ATR stop, rank-hysteresis
exits) but the ranking is a configurable BLEND of price-only factors, so the battery grid SEARCHES the
factor zoo:
  - MOM   : 6-1 momentum (Jegadeesh-Titman)
  - REV   : short-term reversal (negative of last ~21d return; the classic small-cap factor)
  - LOWVOL: low realised volatility (Frazzini-Pedersen / low-vol anomaly)
  - HIPROX: proximity to the 52-week high (George-Hwang)
Composite = w_mom*z(MOM) + w_rev*z(REV) + w_lowvol*z(-VOL) + w_hp*z(HIPROX), long top-N above trend.
The PARAM_GRID over the weights {0,0.5,1.0} explores pure factors AND blends; --select default uses the
pre-registered balanced default. Pass-1 finding: only the cross-sectional SHAPE deploys on this universe.
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
    for p in (_PROJECT / "data" / "processed" / f"sector_map_{market}.json",
              _PROJECT / "data" / "processed" / "sector_map.json"):
        if p.exists():
            try:
                m = json.load(open(p))
                if m:
                    return tuple((k, v) for k, v in m.items() if v)
            except Exception:
                pass
    return tuple()


class CrossSectionalFactor(BaseStrategy):
    """Configurable cross-sectional multi-factor long book (long-only)."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        c = config.get("strategies", {}).get("cross_sectional_factor", {})
        self.mom_lookback = int(c.get("mom_lookback", 126))
        self.mom_skip = int(c.get("mom_skip", 21))
        self.rev_lookback = int(c.get("rev_lookback", 21))
        self.vol_lookback = int(c.get("vol_lookback", 126))
        self.hp_lookback = int(c.get("hp_lookback", 252))
        self.sma_period = int(c.get("sma_period", 200))
        self.atr_period = int(c.get("atr_period", 14))
        self.atr_stop_mult = float(c.get("atr_stop_mult", 3.0))
        self.top_n = int(c.get("top_n", 30))
        self.exit_rank = int(c.get("exit_rank", 60))
        self.max_hold_days = int(c.get("max_hold_days", 90))
        # factor weights (the search dimension)
        self.w_mom = float(c.get("w_mom", 1.0))
        self.w_rev = float(c.get("w_rev", 0.5))
        self.w_lowvol = float(c.get("w_lowvol", 0.5))
        self.w_hp = float(c.get("w_hp", 0.5))
        self.trend_filter = bool(c.get("trend_filter", True))
        self.min_price = float(c.get("min_price", 5.0))
        self.market = config.get("market", "sp500")
        self._sectors = dict(_load_sector_map(self.market))

    @property
    def name(self) -> str:
        return "cross_sectional_factor"

    def precompute(self, data: Dict[str, pd.DataFrame]) -> None:
        for _t, df in data.items():
            if df is None or df.empty or "close" not in df.columns:
                continue
            close = df["close"]
            df["_xf_mom"] = close.shift(self.mom_skip) / close.shift(self.mom_lookback) - 1.0
            df["_xf_rev"] = -(close / close.shift(self.rev_lookback) - 1.0)
            ret = close.pct_change()
            df["_xf_vol"] = ret.rolling(self.vol_lookback, min_periods=max(20, self.vol_lookback // 2)).std()
            df["_xf_hp"] = close / close.rolling(self.hp_lookback, min_periods=max(60, self.hp_lookback // 2)).max()
            df["_xf_sma"] = close.rolling(self.sma_period, min_periods=self.sma_period).mean()
            if {"high", "low"}.issubset(df.columns):
                df["_xf_atr"] = calc_atr(df["high"], df["low"], close, period=self.atr_period)
            else:
                df["_xf_atr"] = ret.rolling(self.atr_period).std() * close
        self._precomputed = True

    def _factor_row(self, df: pd.DataFrame):
        n = len(df)
        if n < max(self.mom_lookback, self.hp_lookback) + 2:
            return None
        last = df.iloc[-1]
        price = last.get("close")
        if price is None or not np.isfinite(price):
            return None
        if "_xf_mom" in df.columns and pd.notna(last.get("_xf_mom")) and pd.notna(last.get("_xf_vol")):
            mom = float(last["_xf_mom"]); rev = float(last.get("_xf_rev", np.nan))
            vol = float(last["_xf_vol"]); hp = float(last.get("_xf_hp", np.nan))
            sma = float(last["_xf_sma"]) if pd.notna(last.get("_xf_sma")) else np.nan
            atr = float(last["_xf_atr"]) if pd.notna(last.get("_xf_atr")) else np.nan
            return mom, rev, vol, hp, sma, atr, float(price)
        c = df["close"].to_numpy(dtype=float)
        mom = c[-1 - self.mom_skip] / c[-1 - self.mom_lookback] - 1.0
        rev = -(c[-1] / c[-1 - self.rev_lookback] - 1.0)
        seg = c[-(self.vol_lookback + 1):]; rets = np.diff(seg) / seg[:-1]
        vol = float(np.std(rets, ddof=0)) if rets.size >= 20 else np.nan
        hp = c[-1] / float(np.max(c[-self.hp_lookback:])) if n >= self.hp_lookback else np.nan
        sma = float(c[-self.sma_period:].mean()) if n >= self.sma_period else np.nan
        if {"high", "low"}.issubset(df.columns):
            atr = float(calc_atr(df["high"], df["low"], df["close"], period=self.atr_period).iloc[-1])
        else:
            atr = (vol * float(price)) if np.isfinite(vol) else np.nan
        return float(mom), float(rev), vol, float(hp), sma, atr, float(price)

    def _rank_universe(self, data: Dict[str, pd.DataFrame]) -> Dict[str, dict]:
        rows = []
        for ticker, df in data.items():
            if df is None or df.empty:
                continue
            fr = self._factor_row(df)
            if fr is None:
                continue
            mom, rev, vol, hp, sma, atr, price = fr
            if price < self.min_price or not np.isfinite(vol) or vol <= 0:
                continue
            if not (np.isfinite(mom) and np.isfinite(rev) and np.isfinite(hp)):
                continue
            rows.append((ticker, mom, rev, vol, hp, sma, atr, price))
        if len(rows) < 5:
            return {}
        arr = lambda i: np.array([r[i] for r in rows])
        mom, rev, vol, hp = arr(1), arr(2), arr(3), arr(4)

        def _z(x):
            sd = x.std(ddof=0)
            return (x - x.mean()) / sd if sd > 0 else np.zeros_like(x)

        composite = (self.w_mom * _z(mom) + self.w_rev * _z(rev)
                     + self.w_lowvol * _z(-vol) + self.w_hp * _z(hp))
        order = np.argsort(-composite)
        out: Dict[str, dict] = {}
        for rank, idx in enumerate(order, start=1):
            t = rows[idx][0]
            out[t] = {"rank": rank, "composite": float(composite[idx]), "mom": float(mom[idx]),
                      "vol": float(vol[idx]), "sma": rows[idx][5], "atr": rows[idx][6], "price": rows[idx][7],
                      "above_trend": (not self.trend_filter) or (np.isfinite(rows[idx][5]) and rows[idx][7] > rows[idx][5])}
        return out

    def generate_signals(self, data, equity, existing_positions) -> List[Signal]:
        signals: List[Signal] = []
        ranks = self._rank_universe(data)
        if not ranks:
            return signals
        held = self._get_held_tickers(existing_positions)
        risk_pct = self.risk_config.get("max_risk_per_trade_pct", 0.005)
        commission = self.fees_config.get("commission_per_trade", 0.0)
        commission_pct = self.fees_config.get("commission_pct", 0.0)
        for ticker, info in sorted(ranks.items(), key=lambda kv: kv[1]["rank"]):
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
            sec = self._sectors.get(ticker, "Unknown")
            signals.append(Signal(
                ticker=ticker, strategy=self.name, direction="long", entry_price=price,
                stop_price=round(stop_price, 4), take_profit=None, position_size=pos["shares"],
                position_value=pos["position_value"], risk_amount=pos["total_risk"],
                confidence=float(min(0.9, 0.5 + 0.4 * (self.top_n - info["rank"] + 1) / self.top_n)),
                rationale=f"{ticker} rank {info['rank']}/{self.top_n}: multi-factor composite {info['composite']:.2f}",
                sector=sec,
                features={"rank": info["rank"], "composite": round(info["composite"], 3), "sector": sec},
                timestamp=datetime.now(),
            ))
        self._logger.info("%s: %d entry signals (ranked %d)", self.name, len(signals), len(ranks))
        return signals

    def check_exits(self, data, positions) -> List[Dict[str, Any]]:
        exits: List[Dict[str, Any]] = []
        ranks = self._rank_universe(data)
        for ticker, pos, df in self._iter_my_positions(data, positions):
            price = float(df["close"].iloc[-1])
            stop_price = pos.get("stop_price", 0) or 0
            days_held = (df.index[-1] - pd.Timestamp(pos["entry_date"])).days
            if stop_price and price <= stop_price:
                exits.append({"ticker": ticker, "reason": "stop_hit", "exit_price": price, "details": "stop"})
                continue
            info = ranks.get(ticker)
            if self.trend_filter and info is not None and np.isfinite(info["sma"]) and price < info["sma"]:
                exits.append({"ticker": ticker, "reason": "signal_exit", "exit_price": price, "details": "trend break"})
                continue
            if info is None or info["rank"] > self.exit_rank:
                exits.append({"ticker": ticker, "reason": "signal_exit", "exit_price": price, "details": "rank exit"})
                continue
            if days_held >= self.max_hold_days:
                exits.append({"ticker": ticker, "reason": "time_exit", "exit_price": price, "details": "max hold"})
        return exits


PARAM_GRID = {
    "w_mom": [0.0, 0.5, 1.0],
    "w_rev": [0.0, 0.5, 1.0],
    "w_lowvol": [0.0, 0.5, 1.0],
    "w_hp": [0.0, 0.5, 1.0],
    "top_n": [15, 20, 30],
    "exit_rank": [40, 60],
    "max_hold_days": [60, 90],
    "trend_filter": [True, False],
}
