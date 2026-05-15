#!/usr/bin/env python3
"""
Поиск инструмента в справочнике Finam и фиксация MVP-символа.

Usage:
    HTTPS_PROXY="" HTTP_PROXY="" poetry run python scripts/find_instrument.py --ticker GZM6
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from trader.auth.client import AsyncAuthClient
from trader.config import Settings
from trader.registry.client import InstrumentRegistry


async def main(ticker: str) -> None:
    settings = Settings()

    async with AsyncAuthClient(
        base_url=settings.finam_api_base_url,
        secret_token=settings.finam_secret_token.get_secret_value(),
        refresh_before_secs=settings.finam_token_refresh_before_secs,
    ) as auth:
        async with InstrumentRegistry(
            base_url=settings.finam_api_base_url,
            get_token=auth.get_token,
        ) as reg:
            print(f"Ищу инструменты с тикером: {ticker}")
            results = await reg.search(ticker)

            if not results:
                print(f"Инструменты с тикером '{ticker}' не найдены.")
                return

            print(f"\nНайдено: {len(results)}\n")
            print(f"{'#':<3} {'Symbol':<20} {'Name':<30} {'Type':<10} {'Archived'}")
            print("-" * 75)
            for i, inst in enumerate(results):
                print(f"{i:<3} {inst.symbol:<20} {inst.name:<30} {inst.type:<10} {inst.is_archived}")

            print()

            active = [r for r in results if not r.is_archived]
            if not active:
                print("Все найденные инструменты архивные.")
                return

            if len(active) == 1:
                chosen = active[0]
                print(f"Единственный активный инструмент: {chosen.symbol}")
            else:
                idx = input(f"Введите номер инструмента (0-{len(results)-1}): ")
                chosen = results[int(idx)]

            print(f"\nЗагружаю детали для {chosen.symbol}...")
            detail = await reg.get_detail(chosen.symbol, account_id=settings.finam_account_id)
            params = await reg.get_params(chosen.symbol, account_id=settings.finam_account_id)

            print(f"  Лот:         {detail.lot_size}")
            print(f"  Шаг цены:    {detail.min_step}")
            print(f"  Экспирация:  {detail.expiration_date}")
            print(f"  Валюта:      {detail.quote_currency}")
            print(f"  Доступен:    {params.is_tradable}")
            print(f"  ГО лонг:     {params.long_initial_margin} {detail.quote_currency}")
            print(f"  ГО шорт:     {params.short_initial_margin} {detail.quote_currency}")

            if not params.is_tradable:
                print(f"\nВНИМАНИЕ: инструмент {chosen.symbol} недоступен для торговли.")

            env_path = Path.home() / ".shectory_trade.env"
            lines = env_path.read_text().splitlines() if env_path.exists() else []
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("FINAM_MVP_SYMBOL="):
                    new_lines.append(f"FINAM_MVP_SYMBOL={chosen.symbol}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"FINAM_MVP_SYMBOL={chosen.symbol}")
            env_path.write_text("\n".join(new_lines) + "\n")
            print(f"\nСохранено: FINAM_MVP_SYMBOL={chosen.symbol}")

            docs_path = Path(__file__).parent.parent / "docs" / "config" / "MVP-instrument.md"
            docs_path.parent.mkdir(parents=True, exist_ok=True)
            docs_path.write_text(f"""# MVP Instrument

**Symbol:** `{chosen.symbol}`
**Ticker:** {detail.ticker}
**MIC:** {detail.mic}
**Name:** {detail.name}
**Type:** {detail.type}
**Expiration:** {detail.expiration_date}

## Trading Parameters

| Parameter | Value |
|-----------|-------|
| Lot size | {detail.lot_size} |
| Min price step | {detail.min_step} |
| Quote currency | {detail.quote_currency} |
| Is tradable | {params.is_tradable} |
| Long initial margin (GO) | {params.long_initial_margin} {detail.quote_currency} |
| Short initial margin (GO) | {params.short_initial_margin} {detail.quote_currency} |

## Why This Symbol

Выбран как MVP-инструмент для торговой системы Shectory Trader.
Дата выбора: {datetime.now().strftime('%Y-%m-%d')}
Счёт: {settings.finam_account_id}
""")
            print(f"Создан файл: {docs_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Поиск инструмента Finam")
    parser.add_argument("--ticker", required=True, help="Тикер инструмента (например: GZM6)")
    args = parser.parse_args()
    asyncio.run(main(args.ticker))
