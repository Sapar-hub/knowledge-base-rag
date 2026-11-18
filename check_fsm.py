"""check_fsm.py — прогон detect_intent() и route() на наборе фраз (П3).

Сквозной ответ здесь не проверяется: build_messages() и llm_client.py
появляются на П4. Поэтому объект создаётся через __new__: __init__ поднимает
MCP-сессию и клиент LLM, а route() и detect_intent() их не используют.
"""

import json
import logging
import sys
from pathlib import Path

from orchestrator import Orchestrator

CASES = {
    "question": [
        "Какие условия обязательны для включения в трудовой договор?",
        "Можно ли уволить работника в период испытательного срока?",
        "Кто вправе применять типовую форму трудового договора для микропредприятий?",
        # «как проверить» — это вопрос, а не просьба проверить
        "Как проверить договор?",
        "Можно ли проверить договор?",
    ],
    "check": [
        "Проверь договор: оклад 50 000, отпуск 28 дней.",
        "Соответствует ли мой договор статье 57 ТК?",
        "Сверь пункт 5.2 с типовой формой.",
    ],
    "unclear": [
        "а если не заплатят?",
        "и что?",
        "не понял",
        "а в моём случае?",
    ],
}


def bare() -> Orchestrator:
    """Объект без MCP и LLM: для П3 достаточно состояния автомата."""
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator._state = {}
    return orchestrator


def report(ok: bool, expected: str, got: str, text: str, errors: list[str]) -> None:
    flag = "ok" if ok else "FAIL"
    print(f"  [{flag:<4}] {expected:<8} -> {got:<8} {text}")
    if not ok:
        errors.append(f"{expected!r} != {got!r} для {text!r}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    errors: list[str] = []
    orchestrator = bare()

    print("--- detect_intent ---")
    for expected, phrases in CASES.items():
        for phrase in phrases:
            got = orchestrator.detect_intent(phrase)
            report(got == expected, expected, got, phrase, errors)

    long_text = "текст договора " * 20
    got = orchestrator.detect_intent(long_text)
    report(got == "check", "check", got, f"<вставленный текст, {len(long_text)} символов>", errors)

    dataset_path = Path(__file__).with_name("gold_dataset.json")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    print("--- gold_dataset.json: все вопросы должны идти аналитику ---")
    for case in dataset:
        got = orchestrator.detect_intent(case["question"])
        report(got == "question", "question", got, case["question"], errors)

    nonsense = "asdf qwer zxcv"
    got = orchestrator.detect_intent(nonsense)
    report(got == "question", "question", got, nonsense, errors)

    print("--- route: переключение ролей ---")
    orchestrator = bare()
    steps = [
        ("question", "analyst", "обычный вопрос"),
        ("check", "checker", "просьба проверить договор"),
        ("question", "analyst", "снова вопрос"),
        ("unclear", "clarifier", "мутная реплика"),
        ("check", "checker", "после уточнения — проверка"),
        ("check", "analyst", "повторная проверка запрещена: откат"),
        ("unknown", "analyst", "неизвестный интент: состояние по умолчанию"),
    ]
    for intent, expected, note in steps:
        got = orchestrator.route("check_fsm", intent)
        report(got == expected, expected, got, f"{intent}: {note}", errors)

    if errors:
        print(f"\nОШИБОК: {len(errors)}")
        return 1

    print("\nOK: интенты и переходы совпадают с ожидаемыми.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
