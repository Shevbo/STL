"""Пропуск миграции, которой нечего делать.

`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` на таблице ЧУЖОЙ роли падает всегда:
Postgres проверяет владельца ДО IF NOT EXISTS. Каждый старт писал в лог провал
миграции, хотя колонка на месте, и этот шум маскировал настоящий провал — тот,
где колонки действительно нет (real-trade 27.09.2026).
"""
import pytest

from trader.api.app import _ADD_COL_RE, _column_exists


class _Pool:
    def __init__(self, answer=1, boom=False):
        self.answer, self.boom, self.asked = answer, boom, []

    async def fetchval(self, _sql, *args):
        if self.boom:
            raise RuntimeError("каталог недоступен")
        self.asked.append(args)
        return self.answer


def test_разбирает_таблицу_и_колонку():
    m = _ADD_COL_RE.search("ALTER TABLE robots ADD COLUMN IF NOT EXISTS retire_comment TEXT")
    assert m.groups() == ("robots", "retire_comment")


@pytest.mark.asyncio
async def test_существующая_колонка_пропускается():
    pool = _Pool(answer=1)
    assert await _column_exists(pool, "ALTER TABLE robots ADD COLUMN IF NOT EXISTS retire_comment TEXT")
    assert pool.asked == [("robots", "retire_comment")]


@pytest.mark.asyncio
async def test_отсутствующая_колонка_не_пропускается():
    assert not await _column_exists(_Pool(answer=None),
                                    "ALTER TABLE robots ADD COLUMN IF NOT EXISTS новая TEXT")


@pytest.mark.asyncio
async def test_не_ADD_COLUMN_не_трогаем():
    pool = _Pool()
    assert not await _column_exists(pool, "CREATE TABLE x (a int)")
    assert pool.asked == []          # каталог даже не спрашивали


@pytest.mark.asyncio
async def test_каталог_недоступен_решает_сам_ALTER():
    # Не смогли спросить — не пропускаем: пусть падает по-настоящему и видно будет.
    assert not await _column_exists(_Pool(boom=True),
                                    "ALTER TABLE robots ADD COLUMN IF NOT EXISTS c TEXT")
