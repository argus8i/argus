"""Derivatives suite tests: greeks/pricing, GEX, basis/expiry, pairs, VIX regime."""
import math
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from research.derivatives import basis as BZ
from research.derivatives import gex as GX
from research.derivatives import greeks as G
from research.derivatives import pairs as PR
from research.derivatives.vix_regime import VixRegimeFilter, VixTier


# ---------------------------------------------------------------- pricing & greeks
def test_put_call_parity_with_dividend_yield():
    S, K, T, r, q, v = 1000.0, 1050.0, 0.25, 0.065, 0.01, 0.3
    c = G.bs_price(S, K, T, r, v, q, "C")
    p = G.bs_price(S, K, T, r, v, q, "P")
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-9)


def test_greeks_consistency_and_finite_difference():
    S, K, T, r, q, v = 500.0, 500.0, 0.1, 0.065, 0.0, 0.25
    g_c = G.bs_greeks(S, K, T, r, v, q, "C")
    g_p = G.bs_greeks(S, K, T, r, v, q, "P")
    assert g_c["delta"] - g_p["delta"] == pytest.approx(math.exp(-q * T))
    assert g_c["gamma"] == pytest.approx(g_p["gamma"])
    h = 0.01
    fd_gamma = (G.bs_price(S + h, K, T, r, v, q, "C") - 2 * G.bs_price(S, K, T, r, v, q, "C")
                + G.bs_price(S - h, K, T, r, v, q, "C")) / h ** 2
    assert g_c["gamma"] == pytest.approx(fd_gamma, rel=1e-3)
    assert g_c["vega"] > 0


def test_vectorised_pricing_matches_scalar():
    K = np.array([900.0, 1000.0, 1100.0])
    vec = G.bs_price(1000.0, K, 0.2, 0.06, 0.25, 0.0, "C")
    assert vec.shape == (3,)
    assert vec[1] == pytest.approx(G.bs_price(1000.0, 1000.0, 0.2, 0.06, 0.25, 0.0, "C"))


def test_implied_vol_round_trip_and_fail_closed():
    price = G.bs_price(1000.0, 1020.0, 0.3, 0.06, 0.32, 0.0, "C")
    assert G.implied_vol(price, 1000.0, 1020.0, 0.3, 0.06, 0.0, "C") == pytest.approx(0.32, abs=1e-6)
    assert math.isnan(G.implied_vol(0.0001, 1000.0, 500.0, 0.3, 0.06, 0.0, "C"))   # below intrinsic


@pytest.mark.parametrize("a,b,rho", [(0.3, -0.4, 0.0), (0.0, 0.0, 0.5), (0.0, 0.0, -0.7)])
def test_bivariate_normal_identities(a, b, rho):
    got = G.bivariate_normal_cdf(a, b, rho)
    if rho == 0.0:
        exp = G.norm_cdf(a) * G.norm_cdf(b)
    else:
        exp = 0.25 + math.asin(rho) / (2 * math.pi)
    assert got == pytest.approx(exp, abs=1e-7)


def test_bivariate_normal_limits():
    assert G.bivariate_normal_cdf(0.5, 1.2, 1.0) == pytest.approx(G.norm_cdf(0.5))
    assert G.bivariate_normal_cdf(0.5, 1.2, -1.0) == pytest.approx(max(0.0, G.norm_cdf(0.5) + G.norm_cdf(1.2) - 1))


def test_crr_european_converges_to_black_scholes():
    bs = G.bs_price(100.0, 100.0, 1.0, 0.05, 0.2, 0.0, "C")
    assert G.crr_price(100.0, 100.0, 1.0, 0.05, 0.2, 0.0, "C", american=False, steps=2000) == pytest.approx(bs, rel=2e-3)


def test_bjerksund_stensland_call_without_dividend_equals_european():
    bs = G.bs_price(100.0, 95.0, 0.5, 0.06, 0.3, 0.0, "C")
    assert G.bjerksund_stensland_2002(100.0, 95.0, 0.5, 0.06, 0.3, 0.0, "C") == pytest.approx(bs, rel=1e-9)


@pytest.mark.parametrize("S,K,T,r,q,v,kind", [
    (42.0, 40.0, 0.75, 0.04, 0.08, 0.35, "C"),
    (100.0, 110.0, 0.5, 0.065, 0.0, 0.3, "P"),
    (100.0, 90.0, 1.0, 0.08, 0.03, 0.25, "P"),
    (1000.0, 980.0, 0.25, 0.065, 0.04, 0.4, "C"),
])
def test_bjerksund_stensland_tracks_binomial_american(S, K, T, r, q, v, kind):
    # BS2002 is a lower bound (a sub-optimal exercise rule); on this set it sits 0.01%-1.24% below CRR.
    bs2002 = G.bjerksund_stensland_2002(S, K, T, r, v, q, kind)
    crr = G.crr_price(S, K, T, r, v, q, kind, american=True, steps=2000)
    euro = G.bs_price(S, K, T, r, v, q, kind)
    assert bs2002 >= euro - 1e-9
    assert bs2002 == pytest.approx(crr, rel=0.015)
    assert G.bjerksund_stensland_1993(S, K, T, r, v, q, kind) <= bs2002 + 1e-9 <= crr + 1e-6


def test_bjerksund_stensland_1993_reproduces_textbook_value():
    # Haug's worked example: American call, S=42, K=40, T=0.75, r=0.04, b=-0.04 (q=0.08), sigma=0.35.
    assert G.bjerksund_stensland_1993(42.0, 40.0, 0.75, 0.04, 0.35, 0.08, "C") == pytest.approx(5.2704, abs=5e-4)


def test_rate_curve_interpolates_and_fails_closed():
    curve = G.RateCurve([(1 / 365, 0.0650), (30 / 365, 0.0670), (90 / 365, 0.0690)])
    assert curve.rate(15 / 365) == pytest.approx(0.0650 + (0.0670 - 0.0650) * (14 / 29), rel=1e-9)
    assert curve.rate(1.0) == pytest.approx(0.0690)          # flat extrapolation
    with pytest.raises(ValueError):
        G.RateCurve([])


# ---------------------------------------------------------------- GEX
def _chain(S=1000.0, strikes=(900, 950, 1000, 1050, 1100), oi=1000, lot=500, iv=0.3, T=10 / 365):
    rows = []
    for k in strikes:
        rows.append(GX.OptionRow(strike=float(k), kind="C", oi_contracts=oi, lot_size=lot, iv=iv, t_years=T))
        rows.append(GX.OptionRow(strike=float(k), kind="P", oi_contracts=oi, lot_size=lot, iv=iv, t_years=T))
    return rows


def test_gex_unknown_sign_returns_only_absolute():
    res = GX.gamma_exposure(1000.0, _chain(), r=0.065)
    assert res.signed_gex_1pct is None and res.abs_gex_1pct > 0
    assert res.sign_convention == GX.DealerSign.UNKNOWN


def test_gex_units_single_contract():
    row = GX.OptionRow(strike=100.0, kind="C", oi_contracts=1, lot_size=1, iv=0.2, t_years=0.1)
    res = GX.gamma_exposure(100.0, [row], r=0.05, sign=GX.DealerSign.DEALER_LONG_CALLS_SHORT_PUTS)
    gamma = G.bs_greeks(100.0, 100.0, 0.1, 0.05, 0.2, 0.0, "C")["gamma"]
    assert res.signed_gex_1pct == pytest.approx(0.01 * 100.0 ** 2 * gamma)


def test_gamma_flip_found_between_call_heavy_top_and_put_heavy_bottom():
    rows = []
    for k, c_oi, p_oi in [(900, 100, 3000), (950, 200, 2000), (1000, 1000, 1000), (1050, 2000, 200), (1100, 3000, 100)]:
        rows.append(GX.OptionRow(float(k), "C", c_oi, 500, 0.3, 10 / 365))
        rows.append(GX.OptionRow(float(k), "P", p_oi, 500, 0.3, 10 / 365))
    flip = GX.gamma_flip(rows, r=0.065, lo=850.0, hi=1150.0, sign=GX.DealerSign.DEALER_LONG_CALLS_SHORT_PUTS)
    assert flip is not None and 950.0 < flip < 1050.0
    assert GX.gamma_flip(rows, r=0.065, lo=850.0, hi=1150.0) is None        # unknown sign -> no flip


def test_hedge_flow_share_and_pin_risk():
    res = GX.gamma_exposure(1000.0, _chain(), r=0.065)
    share = GX.hedge_flow_share(res.abs_gex_1pct, expected_move_pct=1.0, adv_value_rs=5e9)
    assert share == pytest.approx(res.abs_gex_1pct / 5e9)
    pin = GX.pin_risk(1000.0, _chain(), sigma_annual=0.3)
    assert pin["nearest_strike"] == 1000.0 and 0.0 <= pin["score"] <= 1.0


# ---------------------------------------------------------------- basis & expiry
def test_fair_futures_with_and_without_dividend():
    no_div = BZ.fair_futures(1000.0, 0.065, 30 / 365)
    assert no_div == pytest.approx(1000.0 * math.exp(0.065 * 30 / 365))
    with_div = BZ.fair_futures(1000.0, 0.065, 30 / 365, dividends=[(10 / 365, 20.0), (40 / 365, 5.0)])
    assert with_div == pytest.approx((1000.0 - 20.0 * math.exp(-0.065 * 10 / 365)) * math.exp(0.065 * 30 / 365))


def test_nse_monthly_expiry_last_tuesday_and_holiday_rollback():
    assert BZ.nse_monthly_expiry(2026, 9) == date(2026, 9, 29)
    assert BZ.nse_monthly_expiry(2026, 9, holidays={date(2026, 9, 29)}) == date(2026, 9, 28)


def test_rolling_zscore_needs_full_window_and_excludes_current():
    xs = list(range(25))
    z = BZ.rolling_zscore(xs, window=20)
    assert z[:20] == [None] * 20
    prior = np.array(xs[0:20], dtype=float)
    assert z[20] == pytest.approx((20 - prior.mean()) / prior.std(ddof=1))


def test_carry_guard_and_rollover_fail_closed():
    assert BZ.annualized_carry(1005.0, 1000.0, dte_days=3) is None
    assert BZ.annualized_carry(1005.0, 1000.0, dte_days=30) == pytest.approx(0.005 * 365 / 30)
    assert BZ.rollover_ratio(0, 0, 0) is None
    assert BZ.rollover_ratio(600, 300, 100) == pytest.approx(0.4)


# ---------------------------------------------------------------- pairs
def _coint_pair(n=400, beta=1.5, phi=0.8, seed=11):
    rng = np.random.default_rng(seed)
    x = 100 + np.cumsum(rng.normal(0, 1, n))
    u = np.zeros(n)
    for t in range(1, n):
        u[t] = phi * u[t - 1] + rng.normal(0, 1)
    return 5 + beta * x + u, x


def test_engle_granger_detects_cointegration():
    y, x = _coint_pair()
    res = PR.engle_granger(y, x)
    assert res.cointegrated_5pct and res.beta == pytest.approx(1.5, abs=0.05)


def test_engle_granger_size_on_independent_random_walks():
    rng = np.random.default_rng(5)
    rejections = 0
    for _ in range(200):
        y = np.cumsum(rng.normal(size=250))
        x = np.cumsum(rng.normal(size=250))
        rejections += PR.engle_granger(y, x).cointegrated_5pct
    assert rejections / 200 <= 0.12


def test_ou_half_life_recovers_ar1_coefficient():
    rng = np.random.default_rng(3)
    s = np.zeros(3000)
    for t in range(1, 3000):
        s[t] = 0.9 * s[t - 1] + rng.normal()
    assert PR.ou_half_life(s) == pytest.approx(math.log(2) / -math.log(0.9), rel=0.2)
    assert PR.ou_half_life(np.arange(500.0)) is None          # trending: no reversion


def test_kalman_hedge_ratio_converges():
    y, x = _coint_pair(n=600)
    kf = PR.kalman_hedge_ratio(y, x)
    assert kf["beta"][-1] == pytest.approx(1.5, abs=0.15)


def test_pair_sizing_respects_risk_budget_and_slot_cap():
    sz = PR.size_pair(price_y=250.0, price_x=180.0, beta=1.3, spread_sigma=1.2, z_entry=2.0, z_stop=3.2)
    assert sz.risk_rs <= 1500.0 + 1e-6
    assert sz.gross_notional_rs <= 58333.33 + 1e-6
    with pytest.raises(ValueError):
        PR.size_pair(price_y=250.0, price_x=180.0, beta=1.3, spread_sigma=0.0)
    assert sz.requires_short_leg is True


# ---------------------------------------------------------------- VIX regime
def test_vix_hysteresis_prevents_flapping():
    f = VixRegimeFilter()
    t = datetime(2026, 9, 24, 10, 0)
    assert f.update(16.4, t).tier == VixTier.NORMAL
    assert f.update(16.8, t + timedelta(minutes=1)).tier == VixTier.NORMAL      # inside +0.5 buffer
    assert f.update(17.2, t + timedelta(minutes=2)).tier == VixTier.ELEVATED
    assert f.update(16.2, t + timedelta(minutes=3)).tier == VixTier.ELEVATED    # inside -0.5 buffer
    assert f.update(15.8, t + timedelta(minutes=4)).tier == VixTier.NORMAL


def test_vix_missing_or_stale_fails_closed():
    f = VixRegimeFilter()
    t = datetime(2026, 9, 24, 10, 0)
    assert f.update(None, t).multiplier == 0.0
    f.update(14.0, t)
    stale = f.state_at(t + timedelta(minutes=45))
    assert stale.tier == VixTier.UNKNOWN and stale.multiplier == 0.0
