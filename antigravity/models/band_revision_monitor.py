"""
band_revision_monitor.py - Daily Exchange Circuit Band Revision Detector
Part of the Project Swing Trades framework.
Fulfills Q3 from shared/04_OPEN_QUESTIONS.md.
"""

import json
import math
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple


@dataclass
class BandState:
    ticker: str
    prev_close: float
    upper_circuit: float
    lower_circuit: float
    band_pct: float
    date: str


class BandRevisionMonitor:
    """Monitors daily circuit band revisions across watchlist tickers to detect early exchange surveillance tightening."""

    def __init__(self, history_file: str = "antigravity/logs/band_history.json"):
        self.history_file = history_file
        self.history: Dict[str, List[Dict]] = self._load_history()

    def _load_history(self) -> Dict[str, List[Dict]]:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_history(self):
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        with open(self.history_file, "w") as f:
            json.dump(self.history, f, indent=2)

    @staticmethod
    def calculate_band_pct(prev_close: float, upper_circuit: float) -> float:
        if prev_close <= 0:
            return 0.0
        return round(((upper_circuit - prev_close) / prev_close) * 100, 2)

    def check_ticker(
        self,
        ticker: str,
        prev_close: float,
        upper_circuit: float,
        lower_circuit: float,
        date_str: str,
    ) -> Tuple[str, str, Optional[float], float]:
        """Compares today's circuit band against yesterday's state.

        Returns:
            (status, message, old_band_pct, new_band_pct)
        """
        if (
            prev_close is None
            or upper_circuit is None
            or lower_circuit is None
            or not isinstance(prev_close, (int, float))
            or not isinstance(upper_circuit, (int, float))
            or not isinstance(lower_circuit, (int, float))
            or not math.isfinite(prev_close)
            or not math.isfinite(upper_circuit)
            or not math.isfinite(lower_circuit)
            or prev_close <= 0
            or upper_circuit <= prev_close
            or lower_circuit <= 0
            or lower_circuit >= upper_circuit
        ):
            return "DATA_INVALID", f"DATA_INVALID / FAIL-CLOSED: Invalid price or circuit band values for {ticker}.", None, 0.0

        new_band = self.calculate_band_pct(prev_close, upper_circuit)
        ticker_history = self.history.get(ticker, [])

        old_band = None
        status = "NO_CHANGE"
        message = f"{ticker}: Normal band at {new_band}%"

        # Compare strictly against the prior trading day's record (different date)
        prior_records = [r for r in ticker_history if r.get("date") != date_str]
        if prior_records:
            last_record = prior_records[-1]
            old_band = last_record["band_pct"]

            # Tolerance of 0.25% for tick rounding differences
            if new_band < (old_band - 0.5):
                status = "BAND_NARROWED_ALERT"
                message = (
                    f"CRITICAL SURVEILLANCE WARNING on {ticker}: "
                    f"Circuit band narrowed from {old_band}% to {new_band}% on {date_str}! "
                    "Exchange surveillance intervention detected. Immediate exit recommended."
                )
            elif new_band > (old_band + 0.5):
                status = "BAND_WIDENED"
                message = f"{ticker}: Circuit band widened from {old_band}% to {new_band}% on {date_str}."

        # Update existing record for date_str or append to history idempotently
        current_state = BandState(
            ticker=ticker,
            prev_close=prev_close,
            upper_circuit=upper_circuit,
            lower_circuit=lower_circuit,
            band_pct=new_band,
            date=date_str,
        )
        current_dict = asdict(current_state)
        existing_idx = next((i for i, r in enumerate(ticker_history) if r.get("date") == date_str), None)
        if existing_idx is not None:
            self.history[ticker][existing_idx] = current_dict
        else:
            if ticker not in self.history:
                self.history[ticker] = []
            self.history[ticker].append(current_dict)
        self._save_history()

        return status, message, old_band, new_band


if __name__ == "__main__":
    print("--- BAND REVISION MONITOR TEST ---")
    monitor = BandRevisionMonitor(history_file="antigravity/logs/test_band_history.json")

    # Day 1: CHANDRIMA on 26 Aug (20% circuit band)
    # Prev close: 9.28, Upper circuit: 11.13 -> (11.13 - 9.28) / 9.28 = 19.93% (~20%)
    st1, msg1, old1, new1 = monitor.check_ticker(
        ticker="CHANDRIMA",
        prev_close=9.28,
        upper_circuit=11.13,
        lower_circuit=7.43,
        date_str="2026-08-26",
    )
    print(f"26-Aug: Status={st1} | Band={new1}% | Msg={msg1}")

    # Day 2: CHANDRIMA on 27 Aug (Band narrowed 20% -> 10% silently!)
    # Prev close: 11.13, Upper circuit: 12.24 -> (12.24 - 11.13) / 11.13 = 9.97% (~10%)
    st2, msg2, old2, new2 = monitor.check_ticker(
        ticker="CHANDRIMA",
        prev_close=11.13,
        upper_circuit=12.24,
        lower_circuit=10.02,
        date_str="2026-08-27",
    )
    print(f"\n27-Aug: Status={st2} | Old={old2}% -> New={new2}% | Msg={msg2}")

    # Clean up test file
    if os.path.exists("antigravity/logs/test_band_history.json"):
        os.remove("antigravity/logs/test_band_history.json")
