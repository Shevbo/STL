"""Molecule-level P&L validation for the FVG RI robot (paper-fvg-RIM6).

Reads the real live_trades dump (161 fills, RIM6 then RIU6) and computes net P&L
three ways to cross-check, plus the NAIVE single-book number (the phantom bug).

Maker model (live): broker fee 0.45 RUB/contract per fill, NO exchange fee.
Per-contract point value (RIM6 != RIU6).
"""
import csv, sys, os

PV = {"RIM6": 1.438154, "RIU6": 1.449026}
BROKER = 0.45  # RUB per contract, maker

path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/AppData/Local/Temp/fvg_fills.csv")
fills = []
with open(path) as f:
    for row in csv.reader(f):
        if len(row) < 5 or not row[0]:
            continue
        fills.append({"symbol": row[0], "side": row[1], "qty": int(float(row[2])),
                      "price": float(row[3]), "time": int(row[4])})
fills.sort(key=lambda x: x["time"])


def replay(seq, pv):
    """Average-cost replay → (realized_net, closes, end_pos, end_avg, peak)."""
    pos = 0.0; avg = 0.0; carried = 0.0; net = 0.0; closes = 0; peak = 0.0
    for t in seq:
        q = t["qty"]; signed = q if t["side"] == "buy" else -q
        broker = BROKER * q
        if pos == 0:
            avg = t["price"]; pos = signed; carried = broker
        elif (pos > 0) == (signed > 0):
            avg = (avg * abs(pos) + t["price"] * q) / (abs(pos) + q); pos += signed; carried += broker
        else:
            d = 1 if pos > 0 else -1
            close_q = min(abs(pos), q)
            pts = (t["price"] - avg) * close_q if d > 0 else (avg - t["price"]) * close_q
            net += pts * pv - broker - carried; closes += 1
            leftover = q - close_q
            if leftover > 0:
                pos = -d * leftover; avg = t["price"]; carried = broker
            else:
                pos += signed
                if pos == 0:
                    avg = 0.0; carried = 0.0
        peak = max(peak, abs(pos))
    return net, closes, pos, avg, peak


print(f"loaded {len(fills)} fills")
by = {}
for t in fills:
    by.setdefault(t["symbol"], []).append(t)

# ---- Method 1: per-contract realized (strict; carried position left unrealized) ----
total_strict = 0.0
print("\n=== PER-CONTRACT (strict realized) ===")
for sym, seq in by.items():
    net, closes, end_pos, end_avg, peak = replay(seq, PV[sym])
    total_strict += net
    print(f"  {sym}: realized {net:+,.0f} RUB | {closes} closes | end_pos {end_pos:+.0f} @ avg {end_avg:,.0f} | peak {peak:.0f}")
print(f"  TOTAL strict realized = {total_strict:+,.0f} RUB")

# ---- force-close carried positions of EXPIRED contracts at settlement proxy ----
# Append a synthetic flattening fill at the contract's last price and replay, so the
# commission accounting (close fee + carried entry fees) matches lab-analytics exactly.
current = max(by, key=lambda s: by[s][-1]["time"])   # latest-traded contract (RIU6)
total_rollaware = 0.0
print(f"\n=== ROLL force-close (user model) ===")
for sym, seq in by.items():
    net, _, end_pos, end_avg, _ = replay(seq, PV[sym])
    if sym != current and end_pos != 0:
        last = seq[-1]
        synth = dict(symbol=sym, side="sell" if end_pos > 0 else "buy",
                     qty=abs(int(end_pos)), price=last["price"], time=last["time"] + 1)
        net2, _, _, _, _ = replay(seq + [synth], PV[sym])
        print(f"  {sym}: carried {end_pos:+.0f} @ avg {end_avg:,.0f} -> settle @ {last['price']:,.0f}: realized {net:+,.0f} -> {net2:+,.0f} RUB")
        net = net2
    total_rollaware += net
print(f"  TOTAL roll-aware = {total_rollaware:+,.0f} RUB")

# ---- Method PHANTOM: naive single-book over ALL fills (current bug), one pv (RIU6) ----
naive, n_closes, n_pos, _, n_peak = replay(fills, PV["RIU6"])
print(f"\n=== NAIVE single-book (THE BUG, RIU6 pv for all) ===")
print(f"  net {naive:+,.0f} RUB | {n_closes} closes | end_pos {n_pos:+.0f} | peak {n_peak:.0f}")
print(f"  phantom inflation vs roll-aware = {naive - total_rollaware:+,.0f} RUB  ({naive/total_rollaware:.1f}x)" if total_rollaware else "")

print(f"\n=== SUMMARY ===")
print(f"  strict realized (no carry)   : {total_strict:+,.0f}")
print(f"  roll-aware (force-close RIM6) : {total_rollaware:+,.0f}")
print(f"  NAIVE phantom (current UI)    : {naive:+,.0f}")
print(f"  current open position (RIU6)  : {by['RIU6'] and replay(by['RIU6'], PV['RIU6'])[2]:+.0f} contracts")
