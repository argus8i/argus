from datetime import date

import pytest

from research.decision.allocator import Candidate, Position, allocate
from research.decision.sizing import base_qty
from research.decision.stress import band_hit_loss_rs


def candidate(symbol, notional=38000):
    return Candidate('S', symbol, 'BUY', date(2026, 9, 25), 1,
                     notional=notional, cluster=symbol, sector=symbol)


def test_default_size_and_three_slot_boundary():
    assert base_qty(500, 495, 1e6) == 76
    result = allocate([candidate(s) for s in 'ABCD'], free_cash=1e6)
    assert len(result.allocated) == 3
    assert sum(c.notional for c in result.allocated) == 114000
    assert not allocate([candidate('A', 38000.01)], free_cash=1e6).allocated


def test_pending_orders_reserve_slots_and_exposure():
    result = allocate([candidate('C'), candidate('D')], free_cash=1e6,
                      open_positions=[Position('A', 38000, 'A', 'A')],
                      pending_orders=[Position('B', 38000, 'B', 'B')])
    assert len(result.allocated) == 1
    # Legacy oversize holdings still consume their full exposure.
    result = allocate([candidate('C')], free_cash=1e6,
                      open_positions=[Position('A', 50000, 'A', 'A')],
                      pending_orders=[Position('B', 30000, 'B', 'B')])
    assert not result.allocated
    assert result.dropped[0][1] == 'AGGREGATE_EXPOSURE_CAP'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, None, True])
def test_invalid_reserved_exposure_fails_closed(bad):
    with pytest.raises(ValueError):
        allocate([candidate('A')], free_cash=1e6, pending_orders=[Position('B', bad)])


@pytest.mark.parametrize('shock,loss', [(0.10, 11400), (0.15, 17100), (0.20, 22800)])
def test_scenario_losses(shock, loss):
    assert band_hit_loss_rs(band=shock) == pytest.approx(loss)


# ---------------------------------------------------------------------------------------------------------
# Added by Claude (25 Sep 2026) on top of Codex's tests above: exposure book (pending -> active transfer on
# partial fills), absolute long/short exposure, invalid inputs, and the full scenario table and verdict.
from research.decision import stress
from research.decision.allocator import CapacityError, ExposureBook
from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS, MAX_SLOTS, RISK_BUDGET_RS, SLOT_CAP_RS


def test_adjusted_a1_constants_are_the_single_source():
    assert (MAX_SLOTS, SLOT_CAP_RS, AGGREGATE_EXPOSURE_CAP_RS, RISK_BUDGET_RS) == (3, 38000.0, 114000.0, 1500.0)
    assert stress.N_POS == MAX_SLOTS and stress.SCENARIO_BUDGET_RS == 12000.0


def test_risk_budget_still_binds_for_wide_stops():
    assert base_qty(100, 90, 1e6) == 150              # 1500 / 10 = 150 shares = Rs 15,000 < Rs 38,000
    assert base_qty(500, 470, 1e6) == 50              # risk binds (1500 / 30) before the Rs 38,000 cap (76)


@pytest.mark.parametrize('args', [(float('nan'), 495, 1e6), (500, float('inf'), 1e6), (500, 495, None),
                                  (True, 495, 1e6), (-500, -495, 1e6), (500, 500, 1e6), (500, 495, 0)])
def test_base_qty_fails_closed_to_zero(args):
    assert base_qty(*args) == 0


def test_partial_fill_moves_exposure_from_pending_to_active_without_double_count():
    book = ExposureBook()
    book.reserve('o1', 'AAA', 'BUY', 76, 500.0)                     # Rs 38,000 reserved
    assert (book.pending_exposure(), book.active_exposure(), book.slots_used()) == (38000.0, 0.0, 1)
    book.fill('o1', 30, 500.0)                                      # partial fill at the reservation price
    assert book.pending_exposure() == pytest.approx(46 * 500.0)
    assert book.active_exposure() == pytest.approx(30 * 500.0)
    assert book.exposure() == pytest.approx(38000.0)                # transferred, not double counted
    assert book.slots_used() == 1                                   # still one slot
    act, pen = book.positions()
    assert [p.notional for p in act] == [15000.0] and [p.notional for p in pen] == [23000.0]


def test_partial_fill_does_not_release_capacity_early():
    book = ExposureBook()
    book.reserve('o1', 'AAA', 'BUY', 76, 500.0)
    book.reserve('o2', 'BBB', 'SELL', 76, 500.0)
    book.fill('o1', 10, 500.0)
    # the unfilled 66 shares of o1 stay reserved: a third full-size order still fits (114,000) ...
    book.reserve('o3', 'CCC', 'BUY', 76, 500.0)
    assert book.exposure() == pytest.approx(114000.0)
    # ... and nothing more does, even though o1 is only partly filled
    act, pen = book.positions()
    res = allocate([candidate('DDD', 1000)], free_cash=1e6, open_positions=act, pending_orders=pen, max_slots=4)
    assert not res.allocated and res.dropped[0][1] == 'AGGREGATE_EXPOSURE_CAP'
    # cancelling the remainder releases exactly the unfilled reservation and keeps the slot (shares held)
    assert book.cancel('o1') == pytest.approx(66 * 500.0)
    assert book.slots_used() == 3 and book.exposure() == pytest.approx(114000.0 - 33000.0)
    # closing the filled part releases the rest and frees the slot
    assert book.close('o1', 10) == pytest.approx(5000.0)
    assert book.slots_used() == 2 and book.exposure() == pytest.approx(76000.0)


def test_book_positions_feed_allocate_and_a_partial_fill_counts_one_slot():
    book = ExposureBook()
    book.reserve('o1', 'AAA', 'BUY', 76, 500.0)
    book.fill('o1', 40, 499.0)                                      # better price: exposure falls by Rs 40
    assert book.exposure() == pytest.approx(38000.0 - 40.0)
    act, pen = book.positions()
    res = allocate([candidate('BBB'), candidate('CCC'), candidate('DDD')], free_cash=1e6,
                   open_positions=act, pending_orders=pen)
    assert len(res.allocated) == 2                                  # 1 booked slot + 2 new = 3 (equal
    assert [r for _, r in res.dropped] == ['MAX_SLOTS']             # scores: seeded hash picks which two)
    held = allocate([candidate('AAA', 1000)], free_cash=1e6, open_positions=act, pending_orders=pen)
    assert held.dropped[0][1] == 'ALREADY_HELD'
    only_pending = allocate([candidate('ZZZ', 1000)], free_cash=1e6, pending_orders=[Position('ZZZ', 5000)])
    assert only_pending.dropped[0][1] == 'PENDING_ENTRY'


def test_long_and_short_exposure_add_by_absolute_notional():
    book = ExposureBook(max_slots=4)
    book.reserve('L1', 'AAA', 'BUY', 76, 500.0)
    book.reserve('S1', 'BBB', 'SELL', 76, 500.0)
    book.reserve('L2', 'CCC', 'BUY', 76, 500.0)
    with pytest.raises(CapacityError, match='AGGREGATE_EXPOSURE_CAP'):
        book.reserve('S2', 'DDD', 'SELL', 1, 500.0)                 # netting would have allowed it
    res = allocate([candidate('EEE', 500)], free_cash=1e6, max_slots=4,
                   open_positions=[Position('AAA', 38000), Position('BBB', 38000), Position('CCC', 38000)])
    assert res.dropped[0][1] == 'AGGREGATE_EXPOSURE_CAP'


def test_a_worse_fill_is_recorded_as_a_breach_and_blocks_new_reservations():
    book = ExposureBook(max_slots=4)
    book.reserve('o0', 'AAA', 'BUY', 76, 500.0)
    book.reserve('o1', 'BBB', 'BUY', 76, 500.0)
    book.reserve('o2', 'CCC', 'SELL', 76, 500.0)
    book.fill('o2', 76, 502.0)                                      # short filled higher: Rs 38,152
    assert book.breaches and book.exposure() == pytest.approx(114152.0)
    with pytest.raises(CapacityError, match='AGGREGATE_EXPOSURE_CAP'):
        book.reserve('o3', 'DDD', 'BUY', 1, 10.0)                   # even Rs 10 is refused while over the cap
    book.close('o2', 76)                                            # exposure back to Rs 76,000
    book.reserve('o3', 'DDD', 'BUY', 76, 500.0)                     # capacity returns only after the close
    assert book.exposure() == pytest.approx(114000.0)


def test_book_refuses_invalid_inputs():
    book = ExposureBook()
    for bad in [dict(qty=0), dict(qty=1.5), dict(qty=True), dict(reserve_px=float('nan')),
                dict(reserve_px=-1.0), dict(side='HOLD'), dict(qty=77)]:          # 77 x 500 > slot cap
        kw = dict(order_id='x', symbol='AAA', side='BUY', qty=76, reserve_px=500.0)
        kw.update(bad)
        with pytest.raises(CapacityError):
            book.reserve(**kw)
    book.reserve('o1', 'AAA', 'BUY', 76, 500.0)
    with pytest.raises(CapacityError):
        book.reserve('o1', 'BBB', 'BUY', 1, 500.0)                  # duplicate id
    with pytest.raises(CapacityError):
        book.reserve('o9', 'AAA', 'BUY', 1, 500.0)                  # symbol already booked
    for q, px in [(0, 500.0), (77, 500.0), (1, float('inf')), (1, 0.0)]:
        with pytest.raises(ValueError):
            book.fill('o1', q, px)
    with pytest.raises(ValueError):
        book.close('o1', 1)                                         # nothing filled yet
    with pytest.raises(KeyError):
        book.fill('nope', 1, 500.0)


@pytest.mark.parametrize('kw', [dict(free_cash=float('nan')), dict(free_cash=-1.0), dict(free_cash=None),
                                dict(free_cash=1e6, slot_cap_rs=float('inf')),
                                dict(free_cash=1e6, aggregate_cap_rs=-5.0), dict(free_cash=1e6, max_slots=True)])
def test_allocate_refuses_invalid_limits(kw):
    with pytest.raises(ValueError):
        allocate([candidate('A')], **kw)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -38000.0, 0.0, None, True])
def test_invalid_candidate_notional_is_dropped_not_allocated(bad):
    res = allocate([candidate('A', bad)], free_cash=1e6)
    assert not res.allocated and res.dropped[0][1] == 'INVALID_NOTIONAL'


def test_scenario_table_and_verdict():
    rows = {r['shock_pct']: r for r in stress.scenario_table()}
    assert rows[10.0] == {'shock_pct': 10.0, 'exposure_rs': 114000.0, 'loss_rs': 11400.0, 'pct_corpus': 4.56,
                          'budget_rs': 12000.0, 'within_budget': True, 'headroom_rs': 600.0}
    assert (rows[15.0]['loss_rs'], rows[15.0]['pct_corpus'], rows[15.0]['within_budget']) == (17100.0, 6.84, False)
    assert (rows[20.0]['loss_rs'], rows[20.0]['pct_corpus'], rows[20.0]['within_budget']) == (22800.0, 9.12, False)
    assert stress.VERDICT == ('Adjusted A1 satisfies the −10% scenario budget before costs. The −15% and '
                              '−20% scenarios exceed that budget. ₹12,000 is not a guaranteed maximum '
                              'loss, and band flexing does not guarantee an exit.')
    # the verdict must agree with the arithmetic
    assert rows[10.0]['within_budget'] and not rows[15.0]['within_budget'] and not rows[20.0]['within_budget']
    res = stress.run(draws=20_000, nus=(5,), rhos=(0.45,))
    assert res['limits'] == {'max_slots': 3, 'slot_cap_rs': 38000.0, 'aggregate_exposure_cap_rs': 114000.0,
                             'risk_budget_rs': 1500.0}
    assert res['scenarios_before_costs'] == stress.scenario_table() and res['verdict'] == stress.VERDICT


@pytest.mark.parametrize('bad', [float('nan'), -0.1, None, True])
def test_scenario_inputs_fail_closed(bad):
    with pytest.raises(ValueError):
        stress.band_hit_loss_rs(band=bad)
