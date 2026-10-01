"""Пустая книга — это «нечего проверять», а не «данных нет».

ИНЦИДЕНТ 01.10.2026, задержка ВХОДА на три минуты. В 08:01 оператор снял две
последние живые заявки, книга опустела, и `book.codes()` стал пустым множеством.
`any(... for c in ())` это False, поэтому карантин каждую секунду считал, что
рынка не видно, и не двигал отметку «последний раз видели» — хотя данные шли
непрерывно. Через два часа оператор создал тейк на RIZ6, свежесть «вернулась»,
разрыв посчитался в 7601 секунду, и вход был задержан на полный карантин:
залилось по 86090 вместо 86100 тремя минутами ранее.

Карантин защищает от исполнения намерения, УСТАРЕВШЕГО за время слепоты. Пока
живых заявок нет, устаревать нечему.
"""
from trader.quik.blind_quarantine import BlindQuarantine

SEC = 1000


def _fresh_for(codes: set[str], ticks_fresh: bool) -> bool:
    """Та же формула, что в проходе сторожа после фикса."""
    return (not codes) or ticks_fresh


def test_empty_book_does_not_accumulate_a_phantom_gap():
    q = BlindQuarantine()
    now = 1_790_820_000_000
    # книга живая, рынок виден
    q.observe(_fresh_for({"RIZ6"}, True), now)
    # заявки сняты: книга пуста два часа, данные идут
    for _ in range(7200):
        now += SEC
        q.observe(_fresh_for(set(), True), now)
    # оператор создаёт новую заявку
    now += SEC
    q.observe(_fresh_for({"RIZ6"}, True), now)
    assert not q.active(now), (
        f"карантин сработал на пустой книге: разрыв {q.gap_sec} с, "
        "хотя данные шли непрерывно")


def test_real_blindness_still_quarantines():
    """Настоящая слепота обязана по-прежнему задерживать вход."""
    q = BlindQuarantine()
    now = 1_790_820_000_000
    q.observe(_fresh_for({"RIZ6"}, True), now)
    # книга ЖИВАЯ, но кадров нет три часа — это и есть слепота
    for _ in range(3 * 3600):
        now += SEC
        q.observe(_fresh_for({"RIZ6"}, False), now)
    now += SEC
    q.observe(_fresh_for({"RIZ6"}, True), now)
    assert q.active(now), "реальная слепота обязана включать карантин"
    assert q.gap_sec >= 3 * 3600


def test_old_formula_would_have_failed_the_empty_book_case():
    """Доказательство, что тест ловит: прежняя формула (без «нет кодов = видим»)
    на том же сценарии карантин ВКЛЮЧАЕТ."""
    q = BlindQuarantine()
    now = 1_790_820_000_000
    q.observe(True, now)
    for _ in range(7200):                  # прежнее поведение: пусто => False
        now += SEC
        q.observe(False, now)
    now += SEC
    q.observe(True, now)
    assert q.active(now), "иначе тест не доказывает ничего"
    assert q.gap_sec >= 7200
