"""Заявка по УСТАРЕВШЕМУ бару не выставляется.

Планировщик будит робота раз в минуту по настенным часам, а бары приходят с
биржи. Когда торгов нет, get_bars отдаёт последний доступный бар, стратегия
пересчитывается на нём снова и снова и выставляет заявку: 02.08.2026 бумажный
«2EMA · MXU6» продал в воскресенье 19:23 МСК по бару сорокачасовой давности.

Проверка живёт в рантайме, а не в планировщике, чтобы не стоить лишнего запроса:
сравниваются ровно те бары, которые стратегия уже загрузила. Отдельная выкачка
ради проверки заводила второй ключ кэша и для базовых кодов дёргала медленную
склейку — снапшот компаньона уезжал в минуту.
"""
import asyncio

from trader.lab.runtime import LiveRuntime


def _rt(acted: int | None, newest: int | None) -> LiveRuntime:
    rt = LiveRuntime(robot_id="r1", pool=None, paper=True, acted_bar=acted)
    rt.newest_bar = newest
    return rt


def test_same_bar_is_stale():
    assert _rt(acted=1000, newest=1000)._bar_is_stale() is True


def test_older_bar_is_stale():
    # Откат ленты назад не должен переисполнять историю.
    assert _rt(acted=1060, newest=1000)._bar_is_stale() is True


def test_new_bar_is_fresh():
    assert _rt(acted=1000, newest=1060)._bar_is_stale() is False


def test_first_tick_never_trades():
    # Робота ещё не видели: рестарт в выходной иначе сразу отработал бы по
    # пятничному бару — ровно тот случай, который чиним.
    assert _rt(acted=None, newest=1000)._bar_is_stale() is True


def test_strategy_without_bars_is_not_judged():
    # Робот баров не спрашивал (например, портфельный) — судить не о чем.
    assert _rt(acted=None, newest=None)._bar_is_stale() is False


def test_order_on_stale_bar_is_skipped_not_recorded():
    rt = _rt(acted=1000, newest=1000)
    order = asyncio.run(rt.place_order("MXU6", "sell", 1, 226900))
    assert order.status == "skipped"
    assert order.order_id == "skipped-stale-bar"


def test_order_on_fresh_bar_goes_through_paper_path():
    rt = _rt(acted=1000, newest=1060)
    order = asyncio.run(rt.place_order("MXU6", "sell", 1, 226900))
    assert order.status == "paper"          # пул None -> запись пропущена, путь пройден
    assert order.fill_price == 226900
