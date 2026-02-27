"""Ticker mapping between Atlas (.AX) and Moomoo (AU.) formats.

Atlas uses yfinance format:  BHP.AX, CBA.AX, IOZ.AX
Moomoo uses market prefix:   AU.BHP, AU.CBA, AU.IOZ

All conversion happens at the broker boundary — Atlas internals
never see AU. format.
"""


def to_moomoo(ticker: str) -> str:
    """Convert Atlas .AX ticker to Moomoo AU. format.

    >>> to_moomoo('BHP.AX')
    'AU.BHP'
    >>> to_moomoo('AU.BHP')
    'AU.BHP'
    """
    if ticker.startswith("AU."):
        return ticker
    code = ticker.replace(".AX", "").upper()
    return f"AU.{code}"


def to_atlas(moomoo_code: str) -> str:
    """Convert Moomoo AU. ticker to Atlas .AX format.

    Only converts AU. prefix tickers. Other markets (US., HK.) are
    passed through unchanged since Atlas only manages ASX positions.

    >>> to_atlas('AU.BHP')
    'BHP.AX'
    >>> to_atlas('BHP.AX')
    'BHP.AX'
    >>> to_atlas('US.XOP')
    'US.XOP'
    """
    if moomoo_code.endswith(".AX"):
        return moomoo_code
    if moomoo_code.startswith("AU."):
        code = moomoo_code[3:].upper()
        return f"{code}.AX"
    # Non-AU tickers (US., HK.) pass through unchanged
    return moomoo_code


def to_moomoo_list(tickers: list[str]) -> list[str]:
    """Convert list of Atlas tickers to Moomoo format."""
    return [to_moomoo(t) for t in tickers]


def to_atlas_list(moomoo_codes: list[str]) -> list[str]:
    """Convert list of Moomoo codes to Atlas format."""
    return [to_atlas(c) for c in moomoo_codes]
