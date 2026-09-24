"""Тестовый прогон не имеет права сеять журналы в рабочее дерево.

Журнал событий умных заявок пишется побочным эффектом внутри торгового прохода,
поэтому тесты, которые этот проход гоняют, писали бы в data/so_events репозитория."""

import pytest

from trader.quik import so_journal


@pytest.fixture(autouse=True)
def _journal_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(so_journal, "DIR", str(tmp_path / "so_events"))
