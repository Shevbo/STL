#!/usr/bin/env python3
"""
Diagnose Finam [666] "uncovered position may arise/increase".

Strategy: the account holds a real RIM6@RTSX short (-5). [666] fires on orders
that INCREASE uncovered exposure → a further SELL. To capture the full broker
JSON WITHOUT risking a real fill, we place a LIMIT SELL far ABOVE market
(won't execute), qty=1. We log the COMPLETE response/error JSON. If the order
somehow rests, we immediately DELETE it.

Run ONLY when the user has approved a single tiny probe order. Read-only until
the actual POST.
"""
import asyncio
import json

from trader.config import Settings
from trader.auth.client import AsyncAuthClient

import httpx

SYMBOL = "RIM6@RTSX"
PROBE_QTY = 1


async def main():
    s = Settings()
    auth = AsyncAuthClient(
        secret_token=s.finam_secret_token.get_secret_value(),
        base_url=s.finam_api_base_url,
        refresh_before_secs=s.finam_token_refresh_before_secs,
    )
    tok = await auth.get_token()
    acc = s.finam_account_id or auth.account_id
    h = {"Authorization": f"Bearer {tok}"}
    base = s.finam_api_base_url

    async with httpx.AsyncClient(http2=True) as c:
        # 1. current market + position context
        r = await c.get(f"{base}/v1/accounts/{acc}", headers=h, timeout=15)
        acc_d = r.json()
        for p in acc_d.get("positions", []):
            if p.get("symbol") == SYMBOL:
                print("POSITION", SYMBOL, "qty=", p.get("quantity"))
        forts = acc_d.get("portfolio_forts", {})
        print("FORTS available_cash=", forts.get("available_cash"),
              "money_reserved=", forts.get("money_reserved"))

        # last price for a far-OTM limit
        rb = await c.get(
            f"{base}/v1/instruments/{SYMBOL}/quotes/latest"
            if False else f"{base}/v1/assets/{SYMBOL}",
            headers=h, timeout=15,
        )
        print("asset status", rb.status_code)

        # far-above-market limit SELL so it cannot fill (increases short → [666])
        far_price = 200000  # RIM6 ~113k; 200k sell never executes
        body = {
            "symbol": SYMBOL,
            "side": "SIDE_SELL",
            "quantity": {"value": str(PROBE_QTY)},
            "type": "ORDER_TYPE_LIMIT",
            "time_in_force": "TIME_IN_FORCE_DAY",
            "limit_price": {"value": f"{far_price:.1f}"},
            "client_order_id": "diag666probe",
            "comment": "diag666",
        }
        print("\n=== PROBE ORDER (far-OTM limit sell, qty 1) ===")
        print("REQUEST:", json.dumps(body, ensure_ascii=False))
        resp = await c.post(f"{base}/v1/accounts/{acc}/orders/", json=body, headers=h, timeout=20)
        print("HTTP STATUS:", resp.status_code)
        print("FULL RESPONSE BODY:")
        print(resp.text)
        try:
            print("PARSED JSON:", json.dumps(resp.json(), ensure_ascii=False, indent=2))
        except Exception:
            pass

        # if it actually got accepted and rests — cancel immediately
        if resp.is_success:
            d = resp.json()
            oid = d.get("order_id") or d.get("orderId") or (d.get("order") or {}).get("order_id")
            if oid:
                print(f"\nORDER RESTED (id={oid}) — CANCELLING immediately")
                dc = await c.delete(f"{base}/v1/accounts/{acc}/orders/{oid}", headers=h, timeout=15)
                print("CANCEL status:", dc.status_code, dc.text[:300])

    await auth.aclose()


if __name__ == "__main__":
    asyncio.run(main())
