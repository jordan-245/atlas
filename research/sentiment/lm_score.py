#!/usr/bin/env python3
"""Deterministic Loughran-McDonald sentiment scorer (Phase A1 news-sentiment overlay).

Pre-reg: research/brain/hypotheses/news_sentiment_overlay.md. NO LLM — fully reproducible/backtestable
(that is the whole point vs the #215 LLM overlay). Lexicon is FROZEN before the backtest
(research/sentiment/lexicon/lm_*.txt). Builds a daily per-symbol sentiment panel from the ingested
Benzinga parquet shards. Point-in-time: keys on each article's created_at; the proxy applies the
publication lag (no same-day-close look-ahead).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent.parent
LEX = PROJECT / "research" / "sentiment" / "lexicon"
NEWS = PROJECT / "data" / "cache" / "benzinga_news"

_NEGATORS = {"NOT", "NO", "NEVER", "NONE", "NEITHER", "WITHOUT", "LACK", "LACKS", "LACKING",
             "FAILS", "FAILED", "UNABLE", "CANNOT", "WONT", "DONT"}
_TOKEN = re.compile(r"[A-Za-z][A-Za-z\-']+")


@lru_cache(maxsize=1)
def _load_lexicon():
    def _read(p):
        return {w.strip().upper() for w in p.read_text().splitlines()
                if w.strip() and not w.startswith("#")}
    return _read(LEX / "lm_positive.txt"), _read(LEX / "lm_negative.txt")


def tokenize(text: str):
    return [t.upper() for t in _TOKEN.findall(text or "")]


def score_text(text: str) -> dict:
    """Polarity in [-1, 1] with simple 3-token negation flip. Returns pol, npos, nneg, nwords."""
    pos, neg = _load_lexicon()
    toks = tokenize(text)
    npos = nneg = 0
    for i, t in enumerate(toks):
        if t not in pos and t not in neg:
            continue
        negated = any(toks[j] in _NEGATORS for j in range(max(0, i - 3), i))
        is_pos = t in pos
        if negated:
            is_pos = not is_pos
        if is_pos:
            npos += 1
        else:
            nneg += 1
    pol = (npos - nneg) / (npos + nneg + 1.0)
    return {"pol": pol, "npos": npos, "nneg": nneg, "nwords": len(toks)}


def score_articles(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'pol' column from headline + summary."""
    txt = (df["headline"].fillna("") + ". " + df["summary"].fillna(""))
    df = df.copy()
    df["pol"] = [score_text(t)["pol"] for t in txt]
    return df


def daily_symbol_sentiment(universe: set | None = None, news_dir: Path = NEWS) -> pd.DataFrame:
    """Tidy daily per-symbol sentiment from all parquet shards.

    Returns DataFrame [cal_date (UTC date), symbol, sent_mean, sent_sum, news_count].
    cal_date is the calendar date of created_at; the proxy maps it to a trading day with a lag.
    """
    shards = sorted(news_dir.glob("*.parquet"))
    if not shards:
        raise FileNotFoundError(f"no news shards in {news_dir} — run data/benzinga_news.py first")
    frames = []
    for sh in shards:
        df = pd.read_parquet(sh)
        if df.empty:
            continue
        df = score_articles(df)
        df["cal_date"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert("UTC").dt.normalize()
        # explode symbols
        df["symbols"] = df["symbols"].fillna("")
        df = df.assign(symbol=df["symbols"].str.split(",")).explode("symbol")
        df["symbol"] = df["symbol"].str.strip().str.upper()
        df = df[df["symbol"] != ""]
        if universe is not None:
            df = df[df["symbol"].isin(universe)]
        frames.append(df[["cal_date", "symbol", "pol"]])
    if not frames:
        return pd.DataFrame(columns=["cal_date", "symbol", "sent_mean", "sent_sum", "news_count"])
    allr = pd.concat(frames, ignore_index=True)
    g = allr.groupby(["cal_date", "symbol"])["pol"].agg(["mean", "sum", "count"]).reset_index()
    g.columns = ["cal_date", "symbol", "sent_mean", "sent_sum", "news_count"]
    return g


if __name__ == "__main__":
    # smoke
    for s in ["strong growth beats estimates, record profit",
              "bankruptcy risk, lawsuit and decline; weak guidance",
              "not weak; no decline expected"]:
        print(f"{score_text(s)}  <- {s!r}")
