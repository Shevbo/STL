"""Smart-orders engine: pure trigger/trailing/OCO/on-fill/grid logic."""

import pytest

from trader.quik.smart_orders import (
    Cancel,
    Fire,
    SmartOrder,
    SmartOrderBook,
    evaluate,
    marketable_price,
    new_id,
    quantize,
)

NOW = 1_784_700_000_000
STEP = 10.0


def so(**kw):
    base = dict(so_id=new_id(), kind="sl", code="RIU6", side="sell", qty=1,
                created_ms=NOW)
    base.update(kw)
    return SmartOrder(**base)


def run(orders, *, last, bid=0.0, ask=0.0, filled=None, tick_ms=NOW, now=NOW,
        step=STEP):
    return evaluate(orders, "RIU6", last=last, bid=bid or last - STEP,
                    ask=ask or last + STEP, tick_ms=tick_ms, now_ms=now,
                    filled_client_ids=filled or set(), step=step)


# ---- SL / TP triggers ----

def test_sl_long_fires_below_trigger_only():
    o = so(kind="sl", side="sell", trigger_price=83000)
    assert run([o], last=83010) == []
    acts = run([o], last=83000)
    assert len(acts) == 1 and isinstance(acts[0], Fire)
    assert acts[0].price < 83000              # crosses down, marketable
    assert acts[0].price % STEP == 0          # on the exchange grid


def test_sl_short_fires_above_trigger():
    o = so(kind="sl", side="buy", trigger_price=84000)
    assert run([o], last=83990) == []
    acts = run([o], last=84000)
    assert len(acts) == 1 and acts[0].price > 84000 and acts[0].price % STEP == 0


def test_tp_long_fires_at_or_above_target():
    o = so(kind="tp", side="sell", trigger_price=84200)
    assert run([o], last=84190) == []
    acts = run([o], last=84200)
    assert len(acts) == 1 and isinstance(acts[0], Fire)


def test_tp_short_fires_at_or_below_target():
    o = so(kind="tp", side="buy", trigger_price=82000)
    assert run([o], last=82010) == []
    assert len(run([o], last=82000)) == 1


# ---- trailing ----

def test_trail_tp_long_tracks_peak_then_fires_on_retrace():
    o = so(kind="trail_tp", side="sell", trigger_price=0, trail_offset=100)
    assert run([o], last=83000) == []          # activates, peak=83000
    assert run([o], last=83500) == []          # peak=83500
    assert run([o], last=83450) == []          # retrace 50 < 100
    acts = run([o], last=83400)                # retrace 100 -> fire
    assert len(acts) == 1 and isinstance(acts[0], Fire)


def test_trail_tp_activation_level_gates_tracking():
    o = so(kind="trail_tp", side="sell", trigger_price=84000, trail_offset=50)
    # Below activation: a swing down must NOT fire (not armed yet).
    assert run([o], last=83500) == []
    assert run([o], last=83300) == []
    assert not o.activated
    assert run([o], last=84000) == []          # activation
    assert o.activated
    assert run([o], last=84200) == []          # peak 84200
    assert len(run([o], last=84150)) == 1      # retrace 50


def test_trail_tp_short_mirror():
    o = so(kind="trail_tp", side="buy", trigger_price=0, trail_offset=100)
    run([o], last=83000)                        # trough 83000
    assert run([o], last=82500) == []           # trough 82500
    assert len(run([o], last=82600)) == 1       # retrace 100 up -> cover


# ---- on_fill (conditional / order-sends-order) ----

def test_on_fill_waits_for_watched_fill():
    o = so(kind="on_fill", side="sell", watch_client_id="op-entry-1",
           child_price=84300)
    assert run([o], last=84000) == []
    acts = run([o], last=84000, filled={"op-entry-1"})
    assert len(acts) == 1 and acts[0].price == 84300


def test_on_fill_marketable_child_when_no_price_given():
    o = so(kind="on_fill", side="sell", watch_client_id="e1", child_price=0)
    acts = run([o], last=84000, bid=84000, ask=84020, filled={"e1"})
    assert len(acts) == 1
    assert acts[0].price < 84000 and acts[0].price % STEP == 0


# ---- OCO bracket ----

def test_oco_fire_cancels_sibling():
    sl = so(kind="sl", side="sell", trigger_price=83000, oco_group="br1")
    tp = so(kind="tp", side="sell", trigger_price=84500, oco_group="br1")
    acts = run([sl, tp], last=82990)
    kinds = {type(a) for a in acts}
    assert kinds == {Fire, Cancel}
    fired = next(a for a in acts if isinstance(a, Fire))
    cancelled = next(a for a in acts if isinstance(a, Cancel))
    assert fired.so is sl and cancelled.so is tp


# ---- safety rails ----

def test_stale_tick_never_fires():
    o = so(kind="sl", side="sell", trigger_price=83000)
    assert run([o], last=82000, tick_ms=NOW - 60_000, now=NOW) == []


def test_expiry_marks_expired_without_firing():
    o = so(kind="sl", side="sell", trigger_price=83000, good_till_ms=NOW - 1)
    assert run([o], last=82000) == []
    assert o.status == "expired"


def test_grid_quantize_directions():
    assert quantize(83533.12, 10, "sell") == 83530
    assert quantize(83533.12, 10, "buy") == 83540
    assert quantize(86.634, 0.01, "sell") == pytest.approx(86.63)


def test_marketable_cushion_under_collar():
    px = marketable_price("sell", bid=84000, ask=84020, last=84010, step=10)
    assert px % 10 == 0
    assert 0 < (84000 - px) <= 84000 * 0.0015   # crosses, but under the collar


# ---- persistence ----

def test_book_round_trip(tmp_path):
    p = str(tmp_path / "so.json")
    b = SmartOrderBook(p)
    b.add(so(kind="trail_tp", trail_offset=100, peak=83500, activated=True))
    b2 = SmartOrderBook(p)
    b2.load()
    assert len(b2.orders) == 1
    got = b2.orders[0]
    assert got.kind == "trail_tp" and got.peak == 83500 and got.activated


def test_book_corrupt_file_survives(tmp_path):
    p = str(tmp_path / "so.json")
    with open(p, "w") as f:
        f.write("{broken")
    b = SmartOrderBook(p)
    b.load()
    assert b.orders == []


def test_validate_rejects_nonsense():
    assert so(kind="wat").validate()
    assert so(kind="sl", trigger_price=0).validate()
    assert so(kind="trail_tp", trail_offset=0).validate()
    assert so(kind="on_fill", watch_client_id="").validate()
    assert so(kind="sl", trigger_price=83000).validate() is None
