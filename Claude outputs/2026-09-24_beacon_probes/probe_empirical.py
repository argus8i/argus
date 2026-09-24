"""Empirical probe for the BEACON mandate red-team audit (24-Sep-2026). Read-only.

Data: shared/track2_liquid/historical_candles_track2.json (8 stocks + NIFTY50, 32 sessions,
10-Aug to 23-Sep-2026, 15m bars stamped at bar START). This is one regime, a hand-picked
high-beta basket, and far too small to estimate edge. Use it for mechanics only: which gates
bind, how often strategies re-fire, what happens right after a signal bar closes.

Conventions (all conservative for the strategy unless stated):
  * Entry at the signal bar close (an E1 assumption; it flatters every strategy).
  * If a bar touches both stop and target, the stop is taken first.
  * Hard flat at the OPEN of the 15:00 bar (i.e. 15:00, earlier than the 15:10 rule).
  * Cost = 0.106% of entry notional per round trip (MIS, verified in probe_code_claims P1).
  * Daily EMA trend gates are DISABLED: 32 daily bars cannot support EMA50. This only adds signals.
  * Volume baseline = median of the same 15m bucket over the prior <=20 sessions, requiring >=10.
"""
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from antigravity.models.track2_shared_features import SharedFeatureEngine as F  # noqa: E402
from antigravity.models.track2_vwap_reclaim_strategy import VWAPReclaimStrategy  # noqa: E402
from antigravity.models.track2_volatility_squeeze_strategy import VolatilitySqueezeStrategy  # noqa: E402
from antigravity.models.track2_trapdoor_strategy import TrapdoorStrategy  # noqa: E402
from antigravity.models.track2_last_light_strategy import LastLightStrategy  # noqa: E402
from antigravity.models.track2_recoil_strategy import RecoilStrategy  # noqa: E402
from antigravity.models.track2_compass_strategy import CompassStrategy  # noqa: E402
from antigravity.models.track2_alpha_engine import MultiTimeframeAlphaEngine  # noqa: E402
from antigravity.models.market_regime_filter import MarketRegimeSnapshot, MarketRegimeState  # noqa: E402

COST = 0.00106
MIN_PRIOR = 10
FLAT = "15:00"
random.seed(7)

RAW = json.loads((REPO / "shared/track2_liquid/historical_candles_track2.json").read_text(encoding="utf-8"))["symbols"]


def by_day(bars):
    out = defaultdict(list)
    for b in bars:
        out[b["timestamp"][:10]].append(b)
    return out


DATA = {s: by_day(v["bars"]) for s, v in RAW.items()}
DAILY = {s: sorted(v["daily_bars"], key=lambda x: x["timestamp"]) for s, v in RAW.items()}
STOCKS = [s for s in DATA if s != "NIFTY50"]
DAYS = sorted(DATA[STOCKS[0]])
PEERS = {"ANGELONE": "CDSL", "CDSL": "ANGELONE", "INOXWIND": "SUZLON", "SUZLON": "INOXWIND"}


def hm(bar):
    return bar["timestamp"][11:16]


def bucket_median(sym, di, bucket):
    vols = [b["volume"] for d in DAYS[max(0, di - 20):di] for b in DATA[sym][d] if hm(b) == bucket]
    return statistics.median(vols) if len(vols) >= MIN_PRIOR else None


def daily_atr14(sym, day):
    prior = [x for x in DAILY[sym] if x["timestamp"][:10] < day][-15:]
    trs = [max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
           for p, c in zip(prior, prior[1:])]
    return sum(trs) / len(trs) if len(trs) >= 10 else None


def pct(x, n):
    return f"{100 * x / n:5.1f}%" if n else "  n/a"


def boot_ci(xs, fn=statistics.mean, reps=2000):
    if len(xs) < 3:
        return (float("nan"), float("nan"))
    stats = sorted(fn([random.choice(xs) for _ in xs]) for _ in range(reps))
    return stats[int(0.025 * reps)], stats[int(0.975 * reps)]


def simulate(bars, k, side, entry, stop, t1, t2=None, time_bars=None):
    """Net R per full position. t2=None -> single target t1 on 100%.
    Returns (net_R, hit_t1, hit_t2)."""
    sgn = 1 if side == "BUY" else -1
    r = abs(entry - stop)
    if r <= 0:
        return None
    w_open = 1.0
    pnl = 0.0              # in price units per share-weight
    cur_stop = stop
    hit1 = hit2 = False
    last_j = k + time_bars if time_bars else None

    def adverse(b):
        return b["low"] if sgn > 0 else b["high"]

    def favour(b):
        return b["high"] if sgn > 0 else b["low"]

    def crossed_stop(b, s):
        return sgn * (adverse(b) - s) <= 0

    def reached(b, t):
        return sgn * (favour(b) - t) >= 0

    exit_px = None
    for j in range(k + 1, len(bars)):
        b = bars[j]
        if hm(b) >= FLAT:
            exit_px = b["open"]
            break
        if crossed_stop(b, cur_stop):
            fill = b["open"] if sgn * (b["open"] - cur_stop) < 0 else cur_stop   # gap through stop
            pnl += w_open * sgn * (fill - entry)
            w_open = 0.0
            break
        if t2 is None:
            if reached(b, t1):
                pnl += w_open * sgn * (t1 - entry)
                w_open, hit1 = 0.0, True
                break
        else:
            if not hit1 and reached(b, t1):
                pnl += 0.5 * sgn * (t1 - entry)
                w_open, hit1, cur_stop = 0.5, True, entry
                if reached(b, t2):
                    pnl += 0.5 * sgn * (t2 - entry)
                    w_open, hit2 = 0.0, True
                    break
                if crossed_stop(b, cur_stop):          # same bar back to breakeven (conservative)
                    w_open = 0.0
                    break
            elif hit1 and reached(b, t2):
                pnl += 0.5 * sgn * (t2 - entry)
                w_open, hit2 = 0.0, True
                break
        if last_j is not None and j >= last_j:
            exit_px = b["close"]
            break
    if w_open > 0:
        if exit_px is None:
            exit_px = bars[-1]["close"]
        pnl += w_open * sgn * (exit_px - entry)
    return pnl / r - COST * entry / r, hit1, hit2


# ---------------------------------------------------------------- A. intraday volatility profile
print("=== A. Median 15m bar range (% of close) by bar start, 8 stocks x 32 sessions")
rng = defaultdict(list)
for s in STOCKS:
    for d in DAYS:
        for b in DATA[s][d]:
            rng[hm(b)].append(100 * (b["high"] - b["low"]) / b["close"])
prof = {k: statistics.median(v) for k, v in sorted(rng.items())}
print("  " + "  ".join(f"{k}:{v:.2f}" for k, v in prof.items()))
print(f"  09:15 / 12:30 ratio = {prof['09:15'] / prof['12:30']:.2f}x ; 09:30 / 12:30 = {prof['09:30'] / prof['12:30']:.2f}x")

# ---------------------------------------------------------------- B. session-only ATR20 drift
print("\n=== B. SharedFeatureEngine ATR20 (session bars only) vs that bucket's own median range")
for k in (6, 11, 19):
    ratios, atrp = [], []
    for s in STOCKS:
        for d in DAYS:
            bars = DATA[s][d]
            a = F.calculate_atr20(bars[:k + 1])
            atrp.append(100 * a / bars[k]["close"])
            ratios.append((100 * a / bars[k]["close"]) / prof[hm(bars[k])])
    print(f"  at {hm(DATA[STOCKS[0]][DAYS[0]][k])} bar: median ATR20 {statistics.median(atrp):.2f}% of price, "
          f"= {statistics.median(ratios):.2f}x the typical range of the bar being judged")

# ---------------------------------------------------------------- C. run every strategy bar by bar
print("\n=== C. Strategy code run bar-by-bar (sessions with >=10 prior sessions; trend gates disabled)")
REGIME = MarketRegimeSnapshot(state=MarketRegimeState.BULLISH_EXPANSION, nifty_ltp=1.0, nifty_or_high=1.0,
                              nifty_or_low=1.0, ad_ratio=1.5, advances=None, declines=None,
                              reason="probe: most permissive standard regime", allow_standard_orb=True,
                              min_volume_multiple=2.5)
ORB, VW, SQ, TD, LL, RC, CP = (MultiTimeframeAlphaEngine(), VWAPReclaimStrategy(), VolatilitySqueezeStrategy(),
                               TrapdoorStrategy(), LastLightStrategy(), RecoilStrategy(), CompassStrategy())
decisions = defaultdict(Counter)
fires = defaultdict(Counter)
first = defaultdict(dict)
recoil_single_pass = 0
recoil_sides = Counter()
stock_days = 0

for di, d in enumerate(DAYS):
    if di < MIN_PRIOR:
        continue
    nifty = DATA["NIFTY50"][d]
    for s in STOCKS:
        bars = DATA[s][d]
        stock_days += 1
        daily_prior = [x for x in DAILY[s] if x["timestamp"][:10] < d]
        atr14 = daily_atr14(s, d)
        or_h, or_l = bars[0]["high"], bars[0]["low"]
        for k in range(1, len(bars)):
            hist = bars[:k + 1]
            bm = bucket_median(s, di, hm(bars[k]))
            if bm is None:
                continue
            out = {}
            if atr14:
                o = ORB.evaluate_15m_orb(s, bars[k]["close"], or_h, or_l, int(bars[k]["volume"]), int(bm), atr14, REGIME)
                out["ORB"] = (o.decision, o.passed_all_gates, "BUY", o.entry_price, o.stop_price, None, None)
            v = VW.evaluate(s, hist, bm, 0.02, 0.01, 1.0)
            out["VWAP_RECLAIM"] = (v.decision, v.passed_all_gates, "BUY", v.entry_price, v.stop_price,
                                   v.target_tranche1, v.target_tranche2)
            q = SQ.evaluate(s, daily_prior, hist, bm, 0.02, 0.01)
            out["VOL_SQUEEZE"] = (q.decision, q.passed_all_gates, "BUY", q.entry_price, q.stop_price,
                                  q.target_tranche1, q.target_tranche2)
            t = TD.evaluate(s, hist, bm)
            out["TRAPDOOR"] = (t.decision, t.passed_all_gates, "BUY", t.entry_price, t.stop_price, t.target_price, None)
            ll = LL.evaluate_setup(s, hist, bm)
            out["LAST_LIGHT"] = (ll.decision, ll.passed_all_gates, "BUY", ll.entry_price, ll.stop_price,
                                 ll.target_tranche1, ll.target_tranche2)
            rc = RC.evaluate_setup(s, hist, bm)
            out["RECOIL"] = (rc.decision, rc.passed_all_gates, rc.side, rc.entry_price, rc.stop_price, rc.target_price, None)
            if rc.decision == "COST_HURDLE_FAILED":
                fe = F.extract_features(s, hist, bm)
                if abs(fe.session_vwap - fe.close_p) / fe.close_p >= 3 * COST:
                    recoil_single_pass += 1
            if s in PEERS:
                peer = DATA[PEERS[s]][d][:k + 1]
                mkt = [b for b in nifty if b["timestamp"] <= bars[k]["timestamp"]]
                c = CP.evaluate(s, "PAIR", hist, {PEERS[s]: peer}, mkt, bm)
                out["COMPASS"] = (c.decision, c.passed_all_gates, "BUY", c.entry_price, c.stop_price, c.target_price, None)
            for name, (dec, ok, side, e, st, t1, t2) in out.items():
                decisions[name][dec] += 1
                if ok:
                    fires[name][(s, d)] += 1
                    if (s, d) not in first[name]:
                        first[name][(s, d)] = (k, side, e, st, t1, t2)
                        if name == "RECOIL":
                            recoil_sides[side] += 1

print(f"  stock-days evaluated: {stock_days}")
for name in ("ORB", "VWAP_RECLAIM", "VOL_SQUEEZE", "TRAPDOOR", "LAST_LIGHT", "RECOIL", "COMPASS"):
    tot = sum(decisions[name].values())
    top = ", ".join(f"{k}={v}" for k, v in decisions[name].most_common(6))
    fire_days = len(fires[name])
    bars_fired = sum(fires[name].values())
    print(f"  {name:13s} evals {tot:5d} | {top}")
    print(f"  {'':13s} signal stock-days {fire_days} ({pct(fire_days, stock_days)}), signal bars {bars_fired}"
          f" -> {bars_fired / fire_days if fire_days else 0:.1f} bars fire per signalling stock-day")
print(f"  RECOIL cost-hurdle rejections that pass if friction is single-counted: {recoil_single_pass}")
print(f"  RECOIL first-signal sides: {dict(recoil_sides)} (SELL side is rejected by the governor: INVERTED_STOP)")

print("\n=== C2. Outcome of the FIRST signal per stock-day (entry at signal close; illustrative only)")
TIME_STOP = {"TRAPDOOR": 4, "RECOIL": 4, "COMPASS": 6}
for name in ("ORB", "VWAP_RECLAIM", "VOL_SQUEEZE", "TRAPDOOR", "LAST_LIGHT", "RECOIL", "COMPASS"):
    res = []
    for (s, d), (k, side, e, st, t1, t2) in first[name].items():
        bars = DATA[s][d]
        r = abs(e - st)
        if name == "ORB":
            t1 = e + 1.5 * r
            t2 = e + 3.0 * r
        out = simulate(bars, k, side, e, st, t1, t2, TIME_STOP.get(name))
        if out:
            res.append(out)
    if not res:
        print(f"  {name:13s} n=0")
        continue
    rs = [x[0] for x in res]
    n1 = sum(1 for x in res if x[1])
    n2 = sum(1 for x in res if x[2])
    lo, hi = boot_ci(rs)
    print(f"  {name:13s} n={len(rs):3d} mean net R {statistics.mean(rs):+.3f} (95% CI {lo:+.2f}..{hi:+.2f}) "
          f"win {pct(sum(1 for x in rs if x > 0), len(rs))} T1 {pct(n1, len(rs))} T2|T1 {pct(n2, n1) if n1 else '  n/a'}")

# ---------------------------------------------------------------- D. first ORB break events, all 32 sessions
print("\n=== D. First close above the 09:15 bar high, per stock-day, all 32 sessions (no volume gate)")
events = []
for s in STOCKS:
    for d in DAYS:
        bars = DATA[s][d]
        or_h, or_l = bars[0]["high"], bars[0]["low"]
        for k in range(1, len(bars) - 1):
            if hm(bars[k]) >= "14:45":
                break
            if bars[k]["close"] > or_h:
                e, st = bars[k]["close"], or_l
                events.append((s, d, k, e, st, bars[k]["high"] - bars[k]["low"]))
                break
print(f"  events: {len(events)} of {len(STOCKS) * len(DAYS)} stock-days")
riskp = sorted(100 * (e - st) / e for _, _, _, e, st, _ in events)
print(f"  stop distance to OR low: median {statistics.median(riskp):.2f}% (p10 {riskp[len(riskp) // 10]:.2f}%, "
      f"p90 {riskp[9 * len(riskp) // 10]:.2f}%)")
cap_bind = sum(1 for x in riskp if x < 100 * 1500 / 58333)
print(f"  share where the Rs 58,333 cap binds (stop < {100 * 1500 / 58333:.2f}%): {pct(cap_bind, len(riskp))}"
      f" -> realised rupee risk below Rs 1,500 on those trades")
two, one = [], []
for s, d, k, e, st, _ in events:
    bars = DATA[s][d]
    r = e - st
    two.append(simulate(bars, k, "BUY", e, st, e + 1.5 * r, e + 3.0 * r))
    one.append(simulate(bars, k, "BUY", e, st, e + 1.5 * r))
two = [x for x in two if x]
one = [x for x in one if x]
n1 = sum(1 for x in two if x[1])
n2 = sum(1 for x in two if x[2])
print(f"  two-tranche 1.5R/3.0R: mean net R {statistics.mean(x[0] for x in two):+.3f}; P(T1) {pct(n1, len(two))}; "
      f"q = P(T2|T1) {pct(n2, n1)}")
print(f"  single 1.5R target:    mean net R {statistics.mean(x[0] for x in one):+.3f}; P(hit) {pct(sum(1 for x in one if x[1]), len(one))}")

print("\n  D2. Passive entry adverse selection: bid at close - x * signal-bar range, alive for one bar")
print("      outcome measured for the SAME bracket from the signal close, split by whether the bid filled")
for x in (0.10, 0.25, 0.50):
    filled, unfilled, passive_R = [], [], []
    for s, d, k, e, st, rg in events:
        bars = DATA[s][d]
        r = e - st
        res = simulate(bars, k, "BUY", e, st, e + 1.5 * r, e + 3.0 * r)
        if not res:
            continue
        limit = e - x * rg
        if bars[k + 1]["low"] <= limit:
            filled.append(res[0])
            pr = simulate(bars, k, "BUY", limit, st, limit + 1.5 * (limit - st), limit + 3.0 * (limit - st))
            if pr:
                passive_R.append(pr[0] * (limit - st) / r)      # rescale to the marketable order's R
        else:
            unfilled.append(res[0])
            passive_R.append(0.0)
    diff = statistics.mean(filled) - statistics.mean(unfilled)
    diffs = []
    for _ in range(2000):
        a = [random.choice(filled) for _ in filled]
        b = [random.choice(unfilled) for _ in unfilled]
        diffs.append(statistics.mean(a) - statistics.mean(b))
    diffs.sort()
    wins_f = sum(1 for v in filled if v > 0)
    wins_u = sum(1 for v in unfilled if v > 0)
    p_f_w = wins_f / (wins_f + wins_u) if wins_f + wins_u else float("nan")
    p_f_l = (len(filled) - wins_f) / (len(filled) - wins_f + len(unfilled) - wins_u)
    print(f"    x={x:.2f}: P(fill) {pct(len(filled), len(filled) + len(unfilled))}; mean R | filled {statistics.mean(filled):+.3f}, "
          f"| unfilled {statistics.mean(unfilled):+.3f}; gap {diff:+.3f} (95% CI {diffs[50]:+.2f}..{diffs[1950]:+.2f}); "
          f"passive strategy mean R per signal {statistics.mean(passive_R):+.3f}")
    print(f"           P(fill | winner) {100 * p_f_w:.0f}%  vs  P(fill | loser) {100 * p_f_l:.0f}%   "
          f"(win share among fills {pct(wins_f, len(filled))} vs all signals "
          f"{pct(wins_f + wins_u, len(filled) + len(unfilled))})")

# ---------------------------------------------------------------- E. co-movement
print("\n=== E. Within-session 15m log-return correlation (first bar excluded: it carries the overnight gap)")
ret = defaultdict(dict)
for s in STOCKS + ["NIFTY50"]:
    for d in DAYS:
        bars = DATA[s][d]
        for a, b in zip(bars, bars[1:]):
            ret[s][b["timestamp"]] = math.log(b["close"] / a["close"])
keys = sorted(set.intersection(*(set(ret[s]) for s in STOCKS + ["NIFTY50"])))


def corr(x, y):
    mx, my = statistics.mean(x), statistics.mean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


pairs = []
for i, a in enumerate(STOCKS):
    for b in STOCKS[i + 1:]:
        pairs.append(((a, b), corr([ret[a][t] for t in keys], [ret[b][t] for t in keys])))
print(f"  bars: {len(keys)}; mean pairwise stock corr {statistics.mean(c for _, c in pairs):.2f}")
for (a, b), c in sorted(pairs, key=lambda z: -z[1])[:5]:
    print(f"    {a:10s} {b:10s} {c:.2f}")
nif = sorted(keys, key=lambda t: ret["NIFTY50"][t])
tail = nif[:max(1, len(nif) // 20)]
down = [sum(1 for s in STOCKS if ret[s][t] < 0) / len(STOCKS) for t in tail]
sd = {s: statistics.pstdev(ret[s][t] for t in keys) for s in STOCKS}
z = [statistics.mean(ret[s][t] / sd[s] for s in STOCKS) for t in tail]
print(f"  worst 5% NIFTY bars ({len(tail)}): share of the 8 stocks down in the same bar {100 * statistics.mean(down):.0f}%; "
      f"average stock move {statistics.mean(z):+.2f} of its own 15m sd")

# ---------------------------------------------------------------- F. parameter sensitivity
print("\n=== F. Parameter sensitivity: first signal per stock-day, same conventions as C2")


def sweep(name, evaluator, time_bars=None, pairs_only=False):
    firsts = {}
    for di, d in enumerate(DAYS):
        if di < MIN_PRIOR:
            continue
        for s in STOCKS:
            if pairs_only and s not in PEERS:
                continue
            bars = DATA[s][d]
            for k in range(1, len(bars)):
                bm = bucket_median(s, di, hm(bars[k]))
                if bm is None:
                    continue
                sig = evaluator(s, d, di, bars, k, bm)
                if sig:
                    firsts[(s, d)] = (k,) + sig
                    break
    rs = []
    for (s, d), (k, side, e, st, t1, t2) in firsts.items():
        out = simulate(DATA[s][d], k, side, e, st, t1, t2, time_bars)
        if out:
            rs.append(out[0])
    if not rs:
        return f"n=  0"
    lo, hi = boot_ci(rs)
    return f"n={len(rs):3d} mean {statistics.mean(rs):+.3f} (CI {lo:+.2f}..{hi:+.2f})"


def orb_eval(mult):
    reg = MarketRegimeSnapshot(state=MarketRegimeState.BULLISH_EXPANSION, nifty_ltp=1.0, nifty_or_high=1.0,
                               nifty_or_low=1.0, ad_ratio=1.5, advances=None, declines=None, reason="probe",
                               allow_standard_orb=True, min_volume_multiple=mult)

    def ev(s, d, di, bars, k, bm):
        a = daily_atr14(s, d)
        if not a:
            return None
        o = ORB.evaluate_15m_orb(s, bars[k]["close"], bars[0]["high"], bars[0]["low"], int(bars[k]["volume"]), int(bm), a, reg)
        if not o.passed_all_gates:
            return None
        r = o.entry_price - o.stop_price
        return ("BUY", o.entry_price, o.stop_price, o.entry_price + 1.5 * r, o.entry_price + 3.0 * r)
    return ev


def simple(strategy, method, two_tranche, **extra):
    def ev(s, d, di, bars, k, bm):
        hist = bars[:k + 1]
        if method == "vwap":
            x = strategy.evaluate(s, hist, bm, 0.02, 0.01, 1.0)
        elif method == "squeeze":
            x = strategy.evaluate(s, [y for y in DAILY[s] if y["timestamp"][:10] < d], hist, bm, 0.02, 0.01)
        elif method == "compass":
            mkt = [b for b in DATA["NIFTY50"][d] if b["timestamp"] <= bars[k]["timestamp"]]
            x = strategy.evaluate(s, "PAIR", hist, {PEERS[s]: DATA[PEERS[s]][d][:k + 1]}, mkt, bm)
        elif method == "setup":
            x = strategy.evaluate_setup(s, hist, bm)
        else:
            x = strategy.evaluate(s, hist, bm)
        if not x.passed_all_gates:
            return None
        side = getattr(x, "side", "BUY")
        if two_tranche:
            return (side, x.entry_price, x.stop_price, x.target_tranche1, x.target_tranche2)
        return (side, x.entry_price, x.stop_price, x.target_price, None)
    return ev


for m in (2.0, 2.5, 3.5):
    print(f"  ORB volume multiple {m:.1f}x        : {sweep('ORB', orb_eval(m))}")
for m in (1.5, 1.8, 2.5):
    print(f"  VWAP_RECLAIM volume {m:.1f}x        : {sweep('V', simple(VWAPReclaimStrategy(min_volume_mult=m), 'vwap', True))}")
for m in (1.5, 2.0, 3.0):
    print(f"  VOL_SQUEEZE volume {m:.1f}x         : {sweep('S', simple(VolatilitySqueezeStrategy(min_volume_mult=m), 'squeeze', True))}")
for m in (1.0, 1.3, 1.8):
    print(f"  TRAPDOOR min RVOL {m:.1f}          : {sweep('T', simple(TrapdoorStrategy(min_rvol=m), 'plain', False), 4)}")
for m in (0.30, 0.40, 0.50):
    print(f"  LAST_LIGHT min ER8 {m:.2f}        : {sweep('L', simple(LastLightStrategy(min_er8=m), 'setup', True))}")
for m in (1.2, 1.4, 1.6):
    print(f"  RECOIL min stretch {m:.1f} ATR     : {sweep('R', simple(RecoilStrategy(min_stretch_atr=m), 'setup', False), 4)}")
for m in (1.2, 1.5, 2.0):
    print(f"  COMPASS min RVOL {m:.1f}           : {sweep('C', simple(CompassStrategy(min_rvol=m), 'compass', False), 6, True)}")
print("  21 configurations tested: at 5% significance ~1 'significant' result is expected by chance alone.")
