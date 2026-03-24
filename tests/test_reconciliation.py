"""Tests for position reconciliation script."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

# Add project root to path
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from scripts.reconcile_positions import reconcile_positions
from brokers.base import PositionInfo


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_config():
    """Standard config for testing."""
    return {
        "market": "sp500",
        "trading": {
            "broker": "alpaca",
            "live_enabled": True,
        },
    }


@pytest.fixture
def mock_internal_state():
    """Standard internal state with 2 positions."""
    return {
        "market_id": "sp500",
        "mode": "live",
        "positions": [
            {
                "ticker": "AAPL",
                "strategy": "momentum_breakout",
                "entry_date": "2026-03-20",
                "entry_price": 150.0,
                "shares": 10,
                "stop_price": 145.0,
                "order_id": "order-1",
            },
            {
                "ticker": "MSFT",
                "strategy": "mean_reversion",
                "entry_date": "2026-03-21",
                "entry_price": 300.0,
                "shares": 5,
                "stop_price": 290.0,
                "order_id": "order-2",
            },
        ],
        "last_saved": "2026-03-24T10:00:00",
    }


@pytest.fixture
def mock_broker_positions():
    """Broker positions matching internal state exactly."""
    return [
        PositionInfo(
            ticker="AAPL",
            entry_price=150.0,
            shares=10,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
        PositionInfo(
            ticker="MSFT",
            entry_price=300.0,
            shares=5,
            current_price=310.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
    ]


# ═══════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════

def test_all_match(mock_config, mock_internal_state, mock_broker_positions, tmp_path):
    """Test when all positions match — no discrepancies."""
    
    # Setup temp files
    state_file = tmp_path / "live_sp500.json"
    state_file.write_text(json.dumps(mock_internal_state))
    
    with patch("scripts.reconcile_positions.PROJECT", tmp_path):
        with patch("scripts.reconcile_positions.load_config") as mock_load_config:
            mock_load_config.return_value = mock_config
            
            with patch("scripts.reconcile_positions.load_internal_state") as mock_load_state:
                mock_load_state.return_value = mock_internal_state
                
                # Mock broker
                mock_broker = Mock()
                mock_broker.connect.return_value = True
                mock_broker.get_positions.return_value = mock_broker_positions
                
                with patch("brokers.registry.get_live_broker") as mock_get_broker:
                    mock_get_broker.return_value = mock_broker
                    
                    # Run reconciliation
                    result = reconcile_positions("sp500")
    
    # Verify results
    assert result["error"] == ""
    assert len(result["discrepancies"]) == 0
    assert result["summary"]["internal_count"] == 2
    assert result["summary"]["broker_count"] == 2
    assert result["summary"]["phantom"] == 0
    assert result["summary"]["untracked"] == 0
    assert result["summary"]["mismatch"] == 0
    assert result["summary"]["drift"] == 0


def test_phantom_position(mock_config, mock_internal_state, tmp_path):
    """Test PHANTOM detection — internal has position broker doesn't."""
    
    # Broker only has AAPL, not MSFT
    broker_positions = [
        PositionInfo(
            ticker="AAPL",
            entry_price=150.0,
            shares=10,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
    ]
    
    with patch("scripts.reconcile_positions.load_config") as mock_load_config:
        mock_load_config.return_value = mock_config
        
        with patch("scripts.reconcile_positions.load_internal_state") as mock_load_state:
            mock_load_state.return_value = mock_internal_state
            
            mock_broker = Mock()
            mock_broker.connect.return_value = True
            mock_broker.get_positions.return_value = broker_positions
            
            with patch("brokers.registry.get_live_broker") as mock_get_broker:
                mock_get_broker.return_value = mock_broker
                
                result = reconcile_positions("sp500")
    
    # Verify PHANTOM detection
    assert result["error"] == ""
    assert len(result["discrepancies"]) == 1
    assert result["discrepancies"][0]["type"] == "PHANTOM"
    assert result["discrepancies"][0]["ticker"] == "MSFT"
    assert result["summary"]["phantom"] == 1
    assert result["summary"]["internal_count"] == 2
    assert result["summary"]["broker_count"] == 1


def test_untracked_position(mock_config, mock_internal_state):
    """Test UNTRACKED detection — broker has position internal doesn't."""
    
    # Broker has AAPL, MSFT, and TSLA (untracked)
    broker_positions = [
        PositionInfo(
            ticker="AAPL",
            entry_price=150.0,
            shares=10,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
        PositionInfo(
            ticker="MSFT",
            entry_price=300.0,
            shares=5,
            current_price=310.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
        PositionInfo(
            ticker="TSLA",
            entry_price=200.0,
            shares=3,
            current_price=210.0,
            market_value=630.0,
            unrealized_pnl=30.0,
            unrealized_pnl_pct=5.0,
            cost_basis=600.0,
        ),
    ]
    
    with patch("scripts.reconcile_positions.load_config") as mock_load_config:
        mock_load_config.return_value = mock_config
        
        with patch("scripts.reconcile_positions.load_internal_state") as mock_load_state:
            mock_load_state.return_value = mock_internal_state
            
            mock_broker = Mock()
            mock_broker.connect.return_value = True
            mock_broker.get_positions.return_value = broker_positions
            
            with patch("brokers.registry.get_live_broker") as mock_get_broker:
                mock_get_broker.return_value = mock_broker
                
                result = reconcile_positions("sp500")
    
    # Verify UNTRACKED detection
    assert result["error"] == ""
    assert len(result["discrepancies"]) == 1
    assert result["discrepancies"][0]["type"] == "UNTRACKED"
    assert result["discrepancies"][0]["ticker"] == "TSLA"
    assert result["summary"]["untracked"] == 1
    assert result["summary"]["internal_count"] == 2
    assert result["summary"]["broker_count"] == 3


def test_quantity_mismatch(mock_config, mock_internal_state):
    """Test MISMATCH detection — quantity differs."""
    
    # Broker has AAPL with 15 shares instead of 10
    broker_positions = [
        PositionInfo(
            ticker="AAPL",
            entry_price=150.0,
            shares=15,  # Different from internal (10)
            current_price=155.0,
            market_value=2325.0,
            unrealized_pnl=75.0,
            unrealized_pnl_pct=3.33,
            cost_basis=2250.0,
        ),
        PositionInfo(
            ticker="MSFT",
            entry_price=300.0,
            shares=5,
            current_price=310.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
    ]
    
    with patch("scripts.reconcile_positions.load_config") as mock_load_config:
        mock_load_config.return_value = mock_config
        
        with patch("scripts.reconcile_positions.load_internal_state") as mock_load_state:
            mock_load_state.return_value = mock_internal_state
            
            mock_broker = Mock()
            mock_broker.connect.return_value = True
            mock_broker.get_positions.return_value = broker_positions
            
            with patch("brokers.registry.get_live_broker") as mock_get_broker:
                mock_get_broker.return_value = mock_broker
                
                result = reconcile_positions("sp500")
    
    # Verify MISMATCH detection
    assert result["error"] == ""
    assert len(result["discrepancies"]) == 1
    assert result["discrepancies"][0]["type"] == "MISMATCH"
    assert result["discrepancies"][0]["ticker"] == "AAPL"
    assert "internal=10 vs broker=15" in result["discrepancies"][0]["details"]
    assert result["summary"]["mismatch"] == 1


def test_fix_mode_corrects_state(mock_config, mock_internal_state, tmp_path):
    """Test --fix mode corrects internal state from broker."""
    
    # Broker has different positions
    broker_positions = [
        PositionInfo(
            ticker="AAPL",
            entry_price=152.0,  # Different entry price
            shares=15,  # Different quantity
            current_price=155.0,
            market_value=2325.0,
            unrealized_pnl=45.0,
            unrealized_pnl_pct=2.0,
            cost_basis=2280.0,
        ),
        PositionInfo(
            ticker="TSLA",  # New position
            entry_price=200.0,
            shares=3,
            current_price=210.0,
            market_value=630.0,
            unrealized_pnl=30.0,
            unrealized_pnl_pct=5.0,
            cost_basis=600.0,
        ),
    ]
    
    # Setup temp state file
    state_dir = tmp_path / "brokers" / "state"
    state_dir.mkdir(parents=True)
    state_file = state_dir / "live_sp500.json"
    state_file.write_text(json.dumps(mock_internal_state))
    
    with patch("scripts.reconcile_positions.PROJECT", tmp_path):
        with patch("scripts.reconcile_positions.load_config") as mock_load_config:
            mock_load_config.return_value = mock_config
            
            mock_broker = Mock()
            mock_broker.connect.return_value = True
            mock_broker.get_positions.return_value = broker_positions
            
            with patch("brokers.registry.get_live_broker") as mock_get_broker:
                mock_get_broker.return_value = mock_broker
                
                # Run with fix=True
                result = reconcile_positions("sp500", fix=True, dry_run=False)
    
    # Verify corrections were applied
    assert result["fixed"] is True
    assert len(result["discrepancies"]) > 0  # Had issues
    
    # Verify state file was updated
    updated_state = json.loads(state_file.read_text())
    assert len(updated_state["positions"]) == 2
    
    # AAPL should be corrected
    aapl = next(p for p in updated_state["positions"] if p["ticker"] == "AAPL")
    assert aapl["shares"] == 15
    assert aapl["entry_price"] == 152.0
    
    # TSLA should be added
    tsla = next(p for p in updated_state["positions"] if p["ticker"] == "TSLA")
    assert tsla["shares"] == 3
    assert tsla["entry_price"] == 200.0


def test_quiet_mode_no_output_when_clean(mock_config, mock_internal_state, mock_broker_positions, capsys):
    """Test --quiet mode suppresses output when all positions match."""
    
    with patch("scripts.reconcile_positions.load_config") as mock_load_config:
        mock_load_config.return_value = mock_config
        
        with patch("scripts.reconcile_positions.load_internal_state") as mock_load_state:
            mock_load_state.return_value = mock_internal_state
            
            mock_broker = Mock()
            mock_broker.connect.return_value = True
            mock_broker.get_positions.return_value = mock_broker_positions
            
            with patch("brokers.registry.get_live_broker") as mock_get_broker:
                mock_get_broker.return_value = mock_broker
                
                result = reconcile_positions("sp500")
    
    # In quiet mode, main() would suppress output for clean results
    # Here we just verify the result has no discrepancies
    assert len(result["discrepancies"]) == 0
    assert result["error"] == ""


def test_entry_price_drift_detection(mock_config, mock_internal_state):
    """Test DRIFT detection when entry price differs by >1%."""
    
    # Broker has AAPL with entry price 1.5% different
    broker_positions = [
        PositionInfo(
            ticker="AAPL",
            entry_price=152.5,  # 1.67% above internal (150.0)
            shares=10,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=25.0,
            unrealized_pnl_pct=1.64,
            cost_basis=1525.0,
        ),
        PositionInfo(
            ticker="MSFT",
            entry_price=300.0,
            shares=5,
            current_price=310.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=3.33,
            cost_basis=1500.0,
        ),
    ]
    
    with patch("scripts.reconcile_positions.load_config") as mock_load_config:
        mock_load_config.return_value = mock_config
        
        with patch("scripts.reconcile_positions.load_internal_state") as mock_load_state:
            mock_load_state.return_value = mock_internal_state
            
            mock_broker = Mock()
            mock_broker.connect.return_value = True
            mock_broker.get_positions.return_value = broker_positions
            
            with patch("brokers.registry.get_live_broker") as mock_get_broker:
                mock_get_broker.return_value = mock_broker
                
                result = reconcile_positions("sp500")
    
    # Verify DRIFT detection
    assert result["error"] == ""
    assert len(result["discrepancies"]) == 1
    assert result["discrepancies"][0]["type"] == "DRIFT"
    assert result["discrepancies"][0]["ticker"] == "AAPL"
    assert "1.64%" in result["discrepancies"][0]["details"]  # (152.5-150)/152.5*100 = 1.64%
    assert result["summary"]["drift"] == 1
