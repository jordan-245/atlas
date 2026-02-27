"""Atlas Market Profile Base Classes.

Defines the abstract MarketProfile that each exchange must implement,
plus shared data structures for fees, trading hours, etc.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo


@dataclass
class FeeStructure:
    """Broker fee structure for a market.

    Attributes:
        commission_per_trade: Flat fee per trade in local currency.
        commission_pct: Percentage commission (e.g., 0.0003 = 0.03%).
        slippage_pct: Estimated slippage as fraction (e.g., 0.001 = 0.1%).
        flat_fee_threshold: Position value above which pct fee applies.
        min_position_value: Minimum position value allowed.
    """
    commission_per_trade: float = 5.0
    commission_pct: float = 0.0003
    slippage_pct: float = 0.001
    flat_fee_threshold: float = 10000.0
    min_position_value: float = 500.0


@dataclass
class TradingHours:
    """Trading session times (in local exchange timezone).

    Attributes:
        timezone: IANA timezone string.
        market_open: Opening time as "HH:MM".
        market_close: Closing time as "HH:MM".
        pre_market_open: Pre-market start (None if no pre-market).
        post_market_close: Post-market end (None if no post-market).
    """
    timezone: str = "UTC"
    market_open: str = "09:30"
    market_close: str = "16:00"
    pre_market_open: Optional[str] = None
    post_market_close: Optional[str] = None


class MarketProfile(ABC):
    """Abstract base class for exchange/index market profiles.

    Each market (ASX, S&P 500, FTSE, etc.) implements this to provide
    market-specific configuration. Strategies and the backtest engine
    remain market-agnostic — they consume data through this interface.
    """

    # --- Required class attributes (override in subclass) ---

    @property
    @abstractmethod
    def market_id(self) -> str:
        """Short lowercase identifier, e.g. 'asx', 'sp500'."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'ASX 200', 'S&P 500'."""
        ...

    @property
    @abstractmethod
    def country(self) -> str:
        """ISO 3166-1 alpha-2 country code, e.g. 'AU', 'US'."""
        ...

    @property
    @abstractmethod
    def currency(self) -> str:
        """ISO 4217 currency code, e.g. 'AUD', 'USD'."""
        ...

    @property
    @abstractmethod
    def yfinance_suffix(self) -> str:
        """Ticker suffix for yfinance downloads, e.g. '.AX', '', '.L'."""
        ...

    @property
    @abstractmethod
    def benchmark_ticker(self) -> str:
        """Benchmark ETF ticker (fully qualified), e.g. 'IOZ.AX', 'SPY'."""
        ...

    @property
    @abstractmethod
    def risk_free_rate(self) -> float:
        """Annual risk-free rate for Sharpe calculations."""
        ...

    @property
    @abstractmethod
    def trading_hours(self) -> TradingHours:
        """Trading session times for this market."""
        ...

    @property
    @abstractmethod
    def default_fees(self) -> FeeStructure:
        """Default fee structure for this market."""
        ...

    @property
    @abstractmethod
    def trading_days_per_year(self) -> int:
        """Number of trading days per calendar year for this market.

        Used to derive walk-forward backtest windows:
          - train_window = trading_days_per_year  (1 year)
          - test_window  = trading_days_per_year // 4  (~1 quarter)
          - step_days    = trading_days_per_year // 12  (~1 month)
          - min_history  = trading_days_per_year // 4  (~1 quarter)

        Typical values: US/ASX ~252, Japan ~245, HK ~247, UK ~253.
        """
        ...

    @property
    def weekend_days(self) -> tuple:
        """Weekend day numbers (0=Mon, 6=Sun). Override for non-Sat/Sun markets.

        Default: (5, 6) = Saturday, Sunday.
        Middle Eastern markets would use (4, 5) = Friday, Saturday.
        """
        return (5, 6)

    @property
    def operator_timezone(self) -> str:
        """IANA timezone of the human operator (for alert scheduling).

        Override if the operator is in a different timezone from the exchange.
        Default: same as exchange timezone.
        """
        return self.trading_hours.timezone

    @property
    def pre_market_alert_hours_before(self) -> float:
        """How many hours before market open to send the pre-market alert.

        Default: 4.5 hours before open — gives time to review and approve.
        Override per market to get alerts at a reasonable local time.
        """
        return 4.5

    # --- Abstract methods ---

    @abstractmethod
    def get_universe_tickers(self) -> List[str]:
        """Return the full candidate universe of ticker codes (without suffix).

        These are raw codes like 'BHP', 'AAPL'. The suffix is added
        by format_ticker().

        Returns:
            List of ticker code strings.
        """
        ...

    @abstractmethod
    def get_sector_map(self) -> Dict[str, str]:
        """Return a mapping of ticker code -> GICS sector name.

        Returns:
            Dict mapping ticker codes to sector strings.
            Empty dict if not available (will be fetched dynamically).
        """
        ...

    # --- Concrete helpers ---

    def format_ticker(self, code: str) -> str:
        """Add the market suffix to a raw ticker code.

        >>> asx.format_ticker('BHP')
        'BHP.AX'
        >>> sp500.format_ticker('AAPL')
        'AAPL'
        """
        code = code.upper().strip()
        # Already has the suffix
        if self.yfinance_suffix and code.endswith(self.yfinance_suffix):
            return code
        # Strip any existing suffix first
        if "." in code:
            code = code.split(".")[0]
        return f"{code}{self.yfinance_suffix}"

    def strip_suffix(self, ticker: str) -> str:
        """Remove the market suffix from a fully-qualified ticker.

        >>> asx.strip_suffix('BHP.AX')
        'BHP'
        >>> sp500.strip_suffix('AAPL')
        'AAPL'
        """
        if self.yfinance_suffix and ticker.endswith(self.yfinance_suffix):
            return ticker[: -len(self.yfinance_suffix)]
        return ticker

    def get_formatted_tickers(self) -> List[str]:
        """Return universe tickers with the market suffix applied.

        Returns:
            List of fully-qualified ticker strings for yfinance.
        """
        return [self.format_ticker(t) for t in self.get_universe_tickers()]

    def get_backtest_defaults(self) -> Dict[str, int]:
        """Derive walk-forward backtest window defaults from trading days.

        Returns sensible market-aware defaults that can be overridden
        by the config's backtest section.

        Returns:
            Dict with train_window_days, test_window_days,
            step_days, and min_history_days.
        """
        tdy = self.trading_days_per_year
        return {
            "train_window_days": tdy,               # 1 year
            "test_window_days": tdy // 4,            # ~1 quarter
            "step_days": tdy // 12,                  # ~1 month
            "min_history_days": tdy // 4,            # ~1 quarter
        }

    def is_weekend(self, weekday: int) -> bool:
        """Check if a weekday number (0=Mon, 6=Sun) is a weekend for this market."""
        return weekday in self.weekend_days

    def now_exchange(self) -> datetime:
        """Current time in the exchange's local timezone."""
        return datetime.now(ZoneInfo(self.trading_hours.timezone))

    def now_operator(self) -> datetime:
        """Current time in the operator's local timezone."""
        return datetime.now(ZoneInfo(self.operator_timezone))

    def exchange_tz(self) -> ZoneInfo:
        """ZoneInfo for the exchange timezone."""
        return ZoneInfo(self.trading_hours.timezone)

    def operator_tz(self) -> ZoneInfo:
        """ZoneInfo for the operator timezone."""
        return ZoneInfo(self.operator_timezone)

    def format_time(self, dt: datetime, include_tz: bool = True) -> str:
        """Format a datetime in the operator's timezone with short tz label.

        >>> market.format_time(dt)
        '07:30 PM AEST'
        """
        op_dt = dt.astimezone(ZoneInfo(self.operator_timezone))
        time_str = op_dt.strftime("%I:%M %p")
        if include_tz:
            time_str += f" {op_dt.strftime('%Z')}"
        return time_str

    def get_cron_schedule(self) -> dict:
        """Return recommended cron times in the operator's timezone.

        Keys: premarket, intraday_start, intraday_end, postclose, weekdays.
        Values: "HH:MM" strings in operator timezone.

        These are derived from trading_hours + pre_market_alert_hours_before.
        """
        from datetime import time, timedelta
        ex_tz = ZoneInfo(self.trading_hours.timezone)
        op_tz = ZoneInfo(self.operator_timezone)

        # Parse exchange times
        open_h, open_m = map(int, self.trading_hours.market_open.split(":"))
        close_h, close_m = map(int, self.trading_hours.market_close.split(":"))

        # Create timezone-aware datetimes for today
        today = datetime.now(ex_tz).date()
        market_open = datetime(today.year, today.month, today.day, open_h, open_m, tzinfo=ex_tz)
        market_close = datetime(today.year, today.month, today.day, close_h, close_m, tzinfo=ex_tz)

        # Pre-market alert in operator timezone
        alert_dt = market_open - timedelta(hours=self.pre_market_alert_hours_before)
        alert_op = alert_dt.astimezone(op_tz)

        # Intraday monitor: from 30min after open to 30min before close
        intra_start = (market_open + timedelta(minutes=30)).astimezone(op_tz)
        intra_end = (market_close - timedelta(minutes=30)).astimezone(op_tz)

        # Post-close: 1 hour after close
        postclose = (market_close + timedelta(hours=1)).astimezone(op_tz)

        # Weekdays (Mon-Fri = 1-5 for cron)
        all_days = set(range(7)) - set(self.weekend_days)
        # Convert Python weekday (0=Mon) to cron weekday (1=Mon, 0 or 7=Sun)
        cron_days = sorted((d + 1) % 7 or 7 for d in all_days)
        cron_days_str = ",".join(str(d) for d in cron_days)

        return {
            "premarket": alert_op.strftime("%H:%M"),
            "intraday_start": intra_start.strftime("%H:%M"),
            "intraday_end": intra_end.strftime("%H:%M"),
            "postclose": postclose.strftime("%H:%M"),
            "weekdays": cron_days_str,
            "operator_tz": self.operator_timezone,
            "exchange_tz": self.trading_hours.timezone,
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} market_id={self.market_id!r} tickers={len(self.get_universe_tickers())}>"
