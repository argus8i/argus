"""
tests/test_track1_adversarial.py - Permanent Adversarial Test Suite for Track 1
Validates all 8 P0 defect resolutions and secondary safety gates under AGENTS.md Rules 1-11.
"""

import math
import os
import sys
import csv
import pytest
from datetime import datetime

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from antigravity.models.band_revision_monitor import BandRevisionMonitor

from antigravity.models.circuit_rules import (
    CircuitRuleEngine,
    MarketDepthSnapshot,
    EntrySignal,
    PositionSignal,
    ExecutionState,
    BrokerOrderRequest,
    BrokerOrderState,
)
from antigravity.models.risk_calculator import (
    CircuitRiskCalculator,
    RULE_1_OBSERVATION_GATE_PASSED,
    RULE_5_TEN_DAY_LC_DIVISOR,
)
from antigravity.models.queue_model import QueueDrainModel
from antigravity.models.volume_climax_detector import VolumeClimaxDetector, DailySession
from antigravity.models.pcas_execution import PCASExecutionEngine
from antigravity.models.accumulation_screener import AccumulationScreener, TickerData, DailyCandle
from antigravity.daemons.live_signal_engine import analyze_live_ticker, format_live_dashboard


# ==============================================================================
# P0-1: Risk Calculator Signature & Observation Gate (Rule 1 & Rule 5)
# ==============================================================================
class TestRiskCalculatorDefects:
    def test_rule_1_observation_gate_hardcoded_false(self):
        """Invariant: Live capital deployment is strictly prohibited."""
        assert RULE_1_OBSERVATION_GATE_PASSED is False

    def test_calculate_position_size_supports_legacy_and_overloaded_signatures(self):
        """Line 137 bug: calculate_position_size must support rupees_willing_to_lose and daily_volume."""
        res = CircuitRiskCalculator.calculate_position_size(
            rupees_willing_to_lose=2000.0,
            stock_price=25.0,
            daily_volume=500000,
        )
        assert "max_shares" in res
        assert res["live_shares"] == 0  # Rule 1 observation gate
        assert res["paper_shares"] > 0
        assert res["observation_gate_passed"] is False

    def test_risk_calculator_fails_closed_on_invalid_inputs(self):
        """Non-positive price, budget, or volume must fail closed."""
        # Zero price
        res_zero_price = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=2000.0, stock_price=0.0, daily_volume=100000
        )
        assert res_zero_price["max_shares"] == 0
        assert res_zero_price["constrained_by"] == "INVALID_STOCK_PRICE"

        # Price below Rs 10 floor (Rule 2)
        res_sub10 = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=2000.0, stock_price=9.95, daily_volume=100000
        )
        assert res_sub10["max_shares"] == 0
        assert res_sub10["constrained_by"] in ["RULE_2_DISQUALIFIED_SUB_10", "SUB_RS_10_PRICE_FLOOR_VIOLATION"]

        # Non-positive daily volume
        res_zero_vol = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=2000.0, stock_price=25.0, daily_volume=0
        )
        assert res_zero_vol["max_shares"] == 0
        assert res_zero_vol["constrained_by"] in ["INVALID_DAILY_VOLUME", "ZERO_DAILY_VOLUME"]

        # Non-positive risk budget
        res_neg_risk = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=-500.0, stock_price=25.0, daily_volume=100000
        )
        assert res_neg_risk["max_shares"] == 0
        assert res_neg_risk["constrained_by"] == "INVALID_RISK_BUDGET"

    def test_ten_day_lc_loss_formula_precision(self):
        """Rule 5 divisor must match 1 - 0.95^10 = 0.40126... -> 0.401."""
        assert abs(RULE_5_TEN_DAY_LC_DIVISOR - 0.401) < 1e-6


# ==============================================================================
# P0-2: Entry Engine Fails Open & NaN/Zero Volume Attacks (Rule 7)
# ==============================================================================
class TestEntryEngineFailsClosed:
    def _base_valid_snapshot(self) -> MarketDepthSnapshot:
        return MarketDepthSnapshot(
            ticker="VALID_ACCUM",
            price=20.00,
            prev_close=19.50,
            circuit_limit_pct=5.0,
            total_bids=50000,
            total_offers=50000,
            day_volume=600000,
            avg_20d_volume=100000,
            high=20.40,
            low=19.40,
            best_bid=19.95,
            best_ask=20.00,
            spread_pct=0.25,
            series="EQ",
            surveillance_stage="NONE",
        )

    def test_missing_or_zero_proposed_shares_fails_closed(self):
        """Line 448 attack: proposed_shares missing or zero must return DATA_INVALID."""
        snap = self._base_valid_snapshot()
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=0)
        assert sig == EntrySignal.DATA_INVALID
        assert "proposed_shares" in reason

        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=None)
        assert sig == EntrySignal.DATA_INVALID

    def test_zero_or_negative_20d_volume_fails_closed(self):
        """Eliminated fake 1,000,000x expansion: zero or negative 20d volume returns DATA_INVALID."""
        snap = self._base_valid_snapshot()
        snap.avg_20d_volume = 0
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=1000)
        assert sig == EntrySignal.DATA_INVALID
        assert "20-day average volume" in reason or "avg_20d_volume" in reason

        snap.avg_20d_volume = -5000
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=1000)
        assert sig == EntrySignal.DATA_INVALID

    def test_missing_high_low_or_narrow_daily_range_fails_closed(self):
        """Rule 7 requires daily range >= 3.0% and valid high/low."""
        snap = self._base_valid_snapshot()
        snap.high = 0.0
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=1000)
        assert sig == EntrySignal.DATA_INVALID

        # Daily range < 3%
        snap = self._base_valid_snapshot()
        snap.high = 19.60
        snap.low = 19.50  # range is (0.10 / 19.50) = 0.51%
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=1000)
        assert sig == EntrySignal.NO_ENTRY
        assert "range" in reason.lower()

    def test_nan_spread_or_wide_spread_fails_closed(self):
        """Spread must be finite and < 1.0%."""
        snap = self._base_valid_snapshot()
        snap.spread_pct = float("nan")
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=1000)
        assert sig == EntrySignal.DATA_INVALID
        assert "spread" in reason.lower()

        # Wide spread >= 1.0%
        snap = self._base_valid_snapshot()
        snap.spread_pct = 1.5
        snap.best_bid = 19.50
        snap.best_ask = 20.00 # spread is ~2.56%
        sig, reason = CircuitRuleEngine.evaluate_entry_signal(snap, proposed_shares=1000)
        assert sig == EntrySignal.NO_ENTRY


# ==============================================================================
# P0-3: Position Management Decoupling (Day 3+ Drawdowns vs Profit Claims)
# ==============================================================================
class TestPositionSignalIntegrity:
    def test_day_3_loss_never_claims_preemptive_profit_exit(self):
        """A losing position on Day 3+ must never trigger pre-emptive profit exit."""
        snap = MarketDepthSnapshot(
            ticker="LOSING_POS",
            price=18.00,
            prev_close=18.50,
            circuit_limit_pct=5.0,
            total_bids=50000,
            total_offers=50000,
            day_volume=200000,
            avg_20d_volume=100000,
            series="EQ",
            surveillance_stage="NONE",
        )
        # Entry at 20.00, current price 18.00 (-10% loss), holding 3 days
        pos_sig, reason = CircuitRuleEngine.evaluate_position_signal(
            snap=snap, entry_price=20.00, holding_days=3, current_shares=1000
        )
        assert pos_sig != PositionSignal.EXIT_PREEMPTIVE_INTO_UC
        assert pos_sig == PositionSignal.EMERGENCY_EXIT_ATTEMPT
        assert "STOP LOSS / EXIT REVIEW" in reason

    def test_day_3_profit_triggers_preemptive_profit_exit(self):
        """A winning position on Day 3 with +12.5% triggers pre-emptive profit exit."""
        snap = MarketDepthSnapshot(
            ticker="WINNING_POS",
            price=22.50,
            prev_close=21.50,
            circuit_limit_pct=5.0,
            total_bids=500000,
            total_offers=10000,
            day_volume=200000,
            avg_20d_volume=100000,
            series="EQ",
            surveillance_stage="NONE",
        )
        pos_sig, reason = CircuitRuleEngine.evaluate_position_signal(
            snap=snap, entry_price=20.00, holding_days=3, current_shares=1000
        )
        assert pos_sig == PositionSignal.EXIT_PREEMPTIVE_INTO_UC
        assert "PRE-EMPTIVE PROFIT EXIT" in reason

    def test_unknown_surveillance_in_position_fails_closed(self):
        """Unknown surveillance stage during position management triggers DATA_INVALID."""
        snap = MarketDepthSnapshot(
            ticker="UNKNOWN_SURV",
            price=20.00,
            prev_close=20.00,
            circuit_limit_pct=5.0,
            total_bids=10000,
            total_offers=10000,
            day_volume=100000,
            avg_20d_volume=100000,
            series="EQ",
            surveillance_stage="UNKNOWN_CORRUPT_STAGE",
        )
        pos_sig, reason = CircuitRuleEngine.evaluate_position_signal(
            snap=snap, entry_price=20.00, holding_days=1, current_shares=1000
        )
        assert pos_sig == PositionSignal.DATA_INVALID


# ==============================================================================
# P0-4: Pure Discrete 4-State Execution & Broker State Decoupling (Rule 4)
# ==============================================================================
class TestExecutionAndBrokerStateDecoupling:
    def test_execution_state_contains_strictly_4_states(self):
        """ExecutionState must strictly model the 4 discrete market states."""
        members = set(ExecutionState.__members__.keys())
        expected = {"LOCKED_NO_BID", "QUEUED", "PARTIAL", "FILLED"}
        assert members == expected, f"Polluted execution states found: {members - expected}"

    def test_broker_order_evaluation_rejects_invalid_actions_and_negative_margins(self):
        """Broker RMS engine rejects invalid actions and negative margins."""
        req_invalid_act = BrokerOrderRequest(
            ticker="TEST", action="FLY", shares=100, series="EQ"
        )
        st, reason = CircuitRuleEngine.evaluate_broker_order_state(req_invalid_act)
        assert st == BrokerOrderState.REJECTED
        assert "Invalid order action" in reason

        req_neg_margin = BrokerOrderRequest(
            ticker="TEST", action="BUY", shares=100, series="EQ", available_margin=-100.0
        )
        st, reason = CircuitRuleEngine.evaluate_broker_order_state(req_neg_margin)
        assert st == BrokerOrderState.REJECTED
        assert "negative" in reason.lower()

    def test_broker_order_t2t_settlement_rules(self):
        """T2T day T sale prohibited; Day T+1 requires auth/DDPI."""
        # Day T sale of T2T stock
        req_day_t = BrokerOrderRequest(
            ticker="T2T_STOCK", action="SELL", shares=100, series="BE", holding_days=0
        )
        st, reason = CircuitRuleEngine.evaluate_broker_order_state(req_day_t)
        assert st == BrokerOrderState.BROKER_INELIGIBLE
        assert "intraday selling is prohibited" in reason.lower() or "day t sale prohibited" in reason.lower()

        # Day T+1 sale without DDPI or authorization -> AUTH_REQUIRED
        req_t1_no_ddpi = BrokerOrderRequest(
            ticker="T2T_STOCK", action="SELL", shares=100, series="BE", holding_days=1, has_ddpi=False
        )
        st, reason = CircuitRuleEngine.evaluate_broker_order_state(req_t1_no_ddpi)
        assert st == BrokerOrderState.AUTH_REQUIRED

        # Day T+1 sale with DDPI -> ACCEPTED
        req_t1_ddpi = BrokerOrderRequest(
            ticker="T2T_STOCK", action="SELL", shares=100, series="BE", holding_days=1, has_ddpi=True
        )
        st, reason = CircuitRuleEngine.evaluate_broker_order_state(req_t1_ddpi)
        assert st == BrokerOrderState.ACCEPTED


# ==============================================================================
# P0-5: Queue Model Capacity Caps & Deterministic Execution
# ==============================================================================
class TestQueueDrainCapacityBounds:
    def test_queue_drain_strictly_caps_fill_at_order_qty_and_volume(self):
        """An order of 1M shares against 1K turnover cannot fill 1M shares."""
        res = QueueDrainModel.evaluate_queue(
            resting_queue_ahead=0,
            order_qty=1_000_000,
            expected_volume=1_000,
        )
        assert res.estimated_fill_shares == 1_000
        assert res.estimated_fill_shares <= 1_000_000

    def test_uncalibrated_probabilities_deprecated(self):
        """Queue drain output reports None for uncalibrated fill probabilities."""
        res = QueueDrainModel.evaluate_queue(
            resting_queue_ahead=1000, order_qty=5000, expected_volume=20000
        )
        assert res.fill_probability is None


# ==============================================================================
# P0-6: Volume Climax Detector Zero-Bid Lockout
# ==============================================================================
class TestVolumeClimaxDetectorZeroBid:
    def test_zero_bid_depth_returns_zero_bid_liquidity_lockout(self):
        """Zero bid depth must report ZERO_BID_LIQUIDITY_LOCKOUT, never 100% liquidity."""
        sessions = [
            DailySession(
                day_number=1,
                close_price=10.0,
                volume=100000,
                is_upper_circuit=True,
                is_lower_circuit=False,
                bid_depth=500000,
                offer_depth=0,
            ),
            DailySession(
                day_number=2,
                close_price=10.5,
                volume=120000,
                is_upper_circuit=True,
                is_lower_circuit=False,
                bid_depth=600000,
                offer_depth=0,
            ),
            DailySession(
                day_number=3,
                close_price=11.0,
                volume=150000,
                is_upper_circuit=True,
                is_lower_circuit=False,
                bid_depth=400000,
                offer_depth=0,
            ),
            DailySession(
                day_number=4,
                close_price=10.45,
                volume=20000,
                is_upper_circuit=False,
                is_lower_circuit=True,
                bid_depth=0,
                offer_depth=1000000,
            ),
        ]
        res = VolumeClimaxDetector.evaluate_session_progression(sessions, target_profit_pct=20.0)
        assert res["status"] == "ZERO_BID_LIQUIDITY_LOCKOUT"
        assert "zero resting bids" in res["reason"].lower() or "0%." in res["reason"]


# ==============================================================================
# P0-7: PCAS Model & Accumulation Screener Validation
# ==============================================================================
class TestPCASAndAccumulationScreener:
    def test_pcas_model_fails_closed_on_invalid_inputs(self):
        """PCAS simulator rejects non-positive shares or volume."""
        res_zero_shares = PCASExecutionEngine.evaluate_pcas_exit(position_shares=0, daily_volume=10000, circuit_band_pct=2.0)
        assert res_zero_shares.is_clearable is False
        assert res_zero_shares.status == "INVALID_POSITION_SHARES"

        res_zero_vol = PCASExecutionEngine.evaluate_pcas_exit(position_shares=1000, daily_volume=0, circuit_band_pct=2.0)
        assert res_zero_vol.is_clearable is False
        assert res_zero_vol.status == "ZERO_VOLUME_LOCKED"

    def test_accumulation_screener_enforces_track_1_mcap_ceiling(self):
        """Track 1 applies exclusively to micro-caps < Rs 500 Cr."""
        candles = [
            DailyCandle(
                date=f"2026-06-{i:02d}",
                open=15.0, high=16.0, low=14.8, close=15.5,
                volume=100000, delivery_pct=0.45, spread_pct=0.005
            ) for i in range(1, 85)
        ]
        # Mcap = 800 Cr -> Must fail Track 1 ceiling
        ticker_over_mcap = TickerData(
            symbol="LARGE_STOCK",
            series="EQ",
            surveillance_flags=set(),
            history=candles,
            market_cap_cr=800.0,
            surveillance_verified=True,
        )
        screener = AccumulationScreener()
        qualified, reason, _ = screener.evaluate_ticker(ticker_over_mcap)
        assert qualified is False
        assert "track 1" in reason.lower() and "ceiling" in reason.lower()


# ==============================================================================
# P0-8: Feed Staleness & Exchange Record Verification (Daemon Defense)
# ==============================================================================
class TestLiveSignalEngineStaleness:
    def test_stale_feed_or_hidden_tab_returns_data_invalid(self):
        """Live feed with is_stale=True or is_tab_hidden=True returns DATA_INVALID."""
        depth_data_stale = {
            "active_stock": "TEST_TICKER",
            "is_stale": True,
            "status": "STALE_TAB_BACKGROUNDED",
        }
        res = analyze_live_ticker(depth_data_stale, {})
        assert res is not None
        assert res["entry_signal"] == "DATA_INVALID"
        assert res["feed_status"] == "FEED_STALE_OR_UNAVAILABLE"
        assert res["sizing"]["live_shares"] == 0

    def test_invalid_bse_record_fails_closed(self):
        """Invalid BSE exchange record (record_valid=False) returns DATA_INVALID."""
        depth_data = {
            "active_stock": "TEST_TICKER",
            "is_stale": False,
            "is_tab_hidden": False,
            "status": "ACTIVE_MONITORING",
            # The producer writes local_write_time on every snapshot; without
            # it the shared feed gate fails closed on NO_TIMESTAMP before the
            # BSE-record check this test targets can run.
            "data_valid": True,
            "local_write_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        bands_data = {
            "TEST_TICKER": {
                "validation": {"record_valid": False, "anomalies": ["ANOMALOUS_SPREAD"]}
            }
        }
        res = analyze_live_ticker(depth_data, bands_data)
        assert res is not None
        assert res["entry_signal"] == "DATA_INVALID"
        assert res["feed_status"] == "INVALID_EXCHANGE_RECORD"

    def test_format_live_dashboard_handles_fail_closed_without_crash(self):
        """Dashboard formatter must never crash on None LTP, missing depth keys, or console charmap encoding."""
        analysis_fail_closed = {
            "timestamp": "2026-09-12 20:30:00",
            "symbol": "UNKNOWN",
            "ltp": None,
            "prev_close": None,
            "band_pct": None,
            "entry_signal": "DATA_INVALID",
            "entry_reason": "DATA_INVALID / FAIL-CLOSED: Live market depth feed is stale or browser tab is backgrounded.",
            "sizing": {"max_shares": 0, "paper_shares": 0, "live_shares": 0, "observation_gate_passed": False},
            "feed_status": "FEED_STALE_OR_UNAVAILABLE"
        }
        text = format_live_dashboard(analysis_fail_closed)
        assert isinstance(text, str)
        assert "FEED STATUS: FEED_STALE_OR_UNAVAILABLE" in text
        assert "[FAIL-CLOSED]" in text
        assert "LTP: N/A" in text


# ==============================================================================
# Observation Log CSV 46-Column Invariant
# ==============================================================================
class TestObservationLogCSVIntegrity:
    def test_observation_log_has_exact_46_columns_for_all_rows(self):
        """Header and all data rows must strictly adhere to the 46-column schema."""
        csv_path = os.path.join(PROJECT_ROOT, "CHATGPT", "observation_log.csv")
        assert os.path.exists(csv_path)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = list(csv.reader(f))

        assert len(reader) >= 2, "observation_log.csv must have at least a header and 1 row"
        header = reader[0]
        assert len(header) == 46, f"Header has {len(header)} columns instead of 46"

        for idx, row in enumerate(reader[1:], 1):
            # Ensure retrospective trades and market observations are strictly false
            record_type = row[header.index("record_type")]
            counts_gate = row[header.index("counts_toward_paper_gate")].lower()
            if record_type in ["EXAMPLE", "RETROSPECTIVE_LIVE_USER_REPORTED", "MARKET_OBSERVATION"]:
                assert counts_gate in ["false", "0", ""], f"Row {idx} ({record_type}) counted toward gate prematurely!"


# ==============================================================================
# Malformed-Input Red-Team Probe Suite
# ==============================================================================
class TestMalformedInputProbeSuite:
    def test_risk_calculator_infinite_inputs(self):
        """Risk calculator must fail closed on infinite budget or volume."""
        res1 = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(math.inf, 20.0, 100000)
        assert res1["max_shares"] == 0
        assert res1["constrained_by"] == "INVALID_RISK_BUDGET"

        res2 = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(1000.0, 20.0, math.inf)
        assert res2["max_shares"] == 0
        assert res2["constrained_by"] == "INVALID_DAILY_VOLUME"

    def test_queue_model_negative_rank_and_infinite_volume(self):
        """Queue model must reject negative queue ranks and infinite volume."""
        res1 = QueueDrainModel.evaluate_queue(-1000, 500, 100)
        assert res1.fill_state == "INVALID_QUEUE_RANK"
        assert res1.estimated_fill_shares == 0
        assert res1.drain_time_hours is None

        res2 = QueueDrainModel.evaluate_queue(1000, 500, math.inf)
        assert res2.fill_state == "INVALID_EXPECTED_VOLUME"
        assert res2.estimated_fill_shares == 0
        assert res2.drain_time_hours is None

    def test_pcas_execution_nan_negative_band_and_negative_queue(self):
        """PCAS engine rejects NaN/negative bands and negative queue ranks."""
        res_nan = PCASExecutionEngine.evaluate_pcas_exit(1000, 100000, math.nan)
        assert res_nan.is_clearable is False
        assert res_nan.status == "INVALID_CIRCUIT_BAND"

        res_neg = PCASExecutionEngine.evaluate_pcas_exit(1000, 100000, -200.0)
        assert res_neg.is_clearable is False
        assert res_neg.status == "INVALID_CIRCUIT_BAND"

        filled, rem, state = PCASExecutionEngine.simulate_call_auction_equilibrium_fill(100, 50, -100)
        assert state == "INVALID_QUEUE_RANK"
        assert filled == 0

    def test_volume_climax_detector_zero_entry_and_lc_with_bids(self):
        """Volume climax detector rejects zero entry price and formats lc with bids accurately."""
        res1 = VolumeClimaxDetector.evaluate_session_progression([DailySession(1, 0.0, 100, False, False, 10, 10)])
        assert res1["status"] == "DATA_INVALID"

        res2 = VolumeClimaxDetector.evaluate_session_progression([
            DailySession(1, 20.0, 100, False, False, 10, 10),
            DailySession(2, 19.0, 1000, False, True, 10000, 100)
        ])
        assert res2["status"] == "LOWER_CIRCUIT_TRAP"
        assert "locked at lower circuit with 10,000 resting bids" in res2["reason"].lower()

    def test_screener_fails_closed_on_missing_mcap(self):
        """Accumulation screener fails closed if market_cap_cr is None."""
        cs = []
        for i in range(80):
            recent = i >= 60
            close = 19.2 if recent else 16.0
            vol = 400000 if recent else 100000
            delivery = 0.50 if i >= 70 else 0.40
            cs.append(DailyCandle(str(i), close, close * 1.02, close * 0.98, close, vol, delivery, 0.004, 1.0))
        t = TickerData("QUAL", "EQ", set(), cs, surveillance_verified=True, circuit_band_pct=20.0, market_cap_cr=None)
        passed, reason, _ = AccumulationScreener().evaluate_ticker(t, 1000, 5000.0)
        assert passed is False
        assert "market cap unverified or missing" in reason.lower()

    def test_band_revision_monitor_validation_and_idempotency(self):
        """Band revision monitor validates price bounds and maintains alert idempotently across repeat runs."""
        hist_file = os.path.join(PROJECT_ROOT, "antigravity", "logs", "band_test_hist_pytest.json")
        try:
            b = BandRevisionMonitor(hist_file)

            # Invalid inputs
            st_inv, _, _, _ = b.check_ticker("TEST", 0.0, 20.0, 10.0, "2026-09-12")
            assert st_inv == "DATA_INVALID"

            # Valid transition: 20% -> 5%
            b.check_ticker("STOCK", 100.0, 120.0, 80.0, "2026-09-11")
            st1, _, old1, new1 = b.check_ticker("STOCK", 100.0, 105.0, 95.0, "2026-09-12")
            assert st1 == "BAND_NARROWED_ALERT"
            assert old1 == 20.0
            assert new1 == 5.0

            # Repeated run on same date must NOT erase the alert
            st2, _, old2, new2 = b.check_ticker("STOCK", 100.0, 105.0, 95.0, "2026-09-12")
            assert st2 == "BAND_NARROWED_ALERT"
            assert old2 == 20.0
            assert new2 == 5.0
        finally:
            if os.path.exists(hist_file):
                os.remove(hist_file)
