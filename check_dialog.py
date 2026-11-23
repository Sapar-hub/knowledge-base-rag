"""check_dialog.py — диалог из трёх реплик и дамп build_messages() (П5, §3.6).

Третья реплика не называет предмет разговора и без истории не разрешается.
Проверяется не содержание ответа, а то, что уходит в модель: на третьей
реплике в сообщениях должны стоять первый вопрос и первый ответ — в том
порядке, в каком они прозвучали.

Запуск (нужен реальный бэкенд, LLM_BACKEND=ollama): python check_dialog.py
"""

import logging
import uuid

import prompts
from orchestrator import Orchestrator

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "mcp", "sentence_transformers", "chromadb"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

QUESTIONS = [
    # Тема: увольнение на испытательном сроке
    "Можно ли уволить работника в период испытательного срока?",
    # Уточнение к первому ответу: «его» — работника, «предупредить» — об увольнении
    "А за сколько дней его нужно предупредить?",
    # Без истории непонятно, кто «он» и откуда «уйти»: ни испытания, ни увольнения
    "А если он сам захочет уйти?",
]

original = prompts.build_messages
captured: dict = {}


def spy(role, question, chunks, history):
    messages = original(role, question, chunks, history)
    captured.update(messages=messages, role=role, question=question)
    return messages


prompts.build_messages = spy


def main() -> None:
    orchestrator = Orchestrator()
    session_id = f"p5-dialog:{uuid.uuid4()}"
    try:
        for question in QUESTIONS:
            print(f"\n— {question}")
            print(orchestrator.handle(session_id, question))
    finally:
        orchestrator.close()

    print("\n--- Дамп build_messages() для третьей реплики ---")
    print(f"роль агента: {captured['role']}")
    for i, message in enumerate(captured["messages"], 1):
        text = " ".join(message["content"].split())
        print(f" {i}. [{message['role']}] {text[:100]}")


if __name__ == "__main__":
    main()
