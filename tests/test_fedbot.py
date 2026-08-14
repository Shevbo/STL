"""Автоответчик окна: что он обязан пропускать мимо.

Пинг-понг двух автоответчиков — не теоретическая опасность: письма ходят за
секунды, лимит сожгло бы за ночь. Метка auto здесь единственный тормоз."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fedbot


def _msg(sender: str, payload: str, auto: bool = False) -> dict:
    body = {"topic": "t", "payload": payload}
    if auto:
        body["auto"] = True
    return {"id": 1, "from": sender, "message": json.dumps(body, ensure_ascii=False)}


def test_answers_letter_from_another_window():
    assert fedbot.should_answer(_msg("stl-ui-ux", "вопрос"), "stl-real-trade")


def test_never_answers_an_automatic_reply():
    assert not fedbot.should_answer(_msg("stl-ui-ux", "ответ", auto=True), "stl-real-trade")


def test_never_answers_itself():
    assert not fedbot.should_answer(_msg("stl-real-trade", "эхо"), "stl-real-trade")


def test_empty_body_is_not_a_letter():
    assert not fedbot.should_answer(_msg("stl-ui-ux", "   "), "stl-real-trade")


def test_broken_json_still_counts_as_text():
    m = {"id": 1, "from": "stl-ui-ux", "message": "просто текст, не json"}
    assert fedbot.should_answer(m, "stl-real-trade")


def test_letter_with_body_instead_of_payload_is_not_lost():
    """stl-dev-spare прислал текст в "body" — форма тела спекой не закреплена."""
    m = {"id": 9, "from": "stl-dev-spare",
         "message": json.dumps({"topic": "выкладка", "body": "нужен релиз"},
                               ensure_ascii=False)}
    assert fedbot.should_answer(m, "stl-real-trade")
    assert fedbot._payload(m)[1] == "нужен релиз"
