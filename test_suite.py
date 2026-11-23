"""
test_suite.py — прогон Gold Dataset и оценка ответов промптом-судьёй.

ПРАКТИКА 6. Что делает студент:
  1. расширить gold_dataset.json с пяти вопросов, написанных на П2, до десяти,
     добавив к каждому эталонный ответ в поле expected
  2. написать JUDGE_PROMPT — промпт-судью по триаде RAG-метрик
  3. реализовать judge() — вызов судьи и разбор его вердикта

Готово: загрузка датасета, прогон через ядро, сводный отчёт.

Критерии приёмки:
  - в gold_dataset.json ровно 10 вопросов, среди них есть заведомо сложные:
    вне исходных данных, на стыке разделов, с неоднозначной формулировкой
  - пять вопросов с П2 сохранены; замена вопроса допускается только с письменным
    объяснением, чем он оказался негоден
  - прогон проходит все 10 без падений
  - судья возвращает три оценки триады на каждый вопрос, а не одну общую
  - в отчёте зафиксирована хотя бы одна пойманная галлюцинация
    (или обоснованно показано, что их нет)
  - обе метрики измерены и разведены: hit-rate@5 (механически, ingest.py, с П2)
    и Context Precision из триады (судьёй, здесь). В отчёте видно, что это
    разные числа, а не одно под двумя именами

Почему судьёй выступает LLM. Классические Accuracy и F1 на свободном тексте
не работают: верный ответ можно сформулировать десятком способов, и посимвольное
сравнение забракует их все. Сильная модель в роли эксперта-оценщика сравнивает
смысл — это и есть LLM-as-a-judge (Л6).

Запуск:
    python test_suite.py
"""

import copy
import json
import os
import re
import statistics
import uuid
from pathlib import Path

from contracts import Chunk
from llm_client import build_client
from orchestrator import Orchestrator
from prompts import format_chunks

DATASET = Path(__file__).parent / "gold_dataset.json"

METRICS = ("faithfulness", "answer_relevance", "context_precision")

# Промпт-судья. Метрики описаны раздельно и с якорями 0 / 0.5 / 1: иначе судья
# выставляет три одинаковых числа — общее впечатление, размноженное на три поля.
JUDGE_PROMPT = """Ты — эксперт-оценщик ответов справочной системы по трудовому праву РФ.
На входе: тип вопроса, вопрос, эталонный ответ, ФРАГМЕНТЫ, которые получила система,
и ОТВЕТ системы. Поставь три НЕЗАВИСИМЫЕ оценки от 0 до 1. Они почти всегда разные:
хороший по смыслу ответ может опираться на плохие фрагменты, и наоборот.

faithfulness — подтверждается ли ОТВЕТ ФРАГМЕНТАМИ (не эталоном и не твоими знаниями).
  1 — все утверждения ответа есть во фрагментах; честный отказ «нет ответа» — тоже 1;
  0.5 — в основном подтверждено, но есть лишние детали, которых во фрагментах нет;
  0 — главное утверждение ответа во фрагментах отсутствует или им противоречит.

answer_relevance — отвечает ли ОТВЕТ на ВОПРОС; сравни по смыслу с эталоном.
  1 — отвечает прямо и совпадает с эталоном по сути (формулировки могут отличаться);
  0.5 — отвечает частично: пропущена часть эталона или много постороннего;
  0 — ответ о другом или ответа по существу нет.

context_precision — относятся ли ФРАГМЕНТЫ к ВОПРОСУ. Ответ здесь не учитывай.
  1 — все фрагменты по теме вопроса; 0.5 — около половины; 0 — ни одного.

Тип out_of_scope: ответа в базе заведомо нет, эталон — отказ.
  Ответ-отказ («В базе знаний нет ответа…»): faithfulness 1, answer_relevance 1 —
  даже если фрагменты не по теме: отказ ничего не утверждает, значит, ничего не выдумал.
  Ответ по существу (система «ответила» вместо отказа): faithfulness 0, answer_relevance 0.
  context_precision для out_of_scope не определён — ставь 0.

Выведи только JSON-объект в одну строку, без текста до и после, вида:
{"faithfulness": <число>, "answer_relevance": <число>, "context_precision": <число>, "comment": "<почему именно такие оценки, своими словами>"}

Образец 1. Вопрос: за сколько работник предупреждает об увольнении по собственному
желанию? Фрагменты: ст. 80 «не позднее чем за две недели» и два фрагмента об охране
труда. Ответ: «За две недели, письменно (ст. 80) [tk_rf.md]».
{"faithfulness": 1, "answer_relevance": 1, "context_precision": 0.33, "comment": "Срок две недели есть во фрагменте ст. 80; ответ совпадает с эталоном; из трёх фрагментов по теме один."}

Образец 2. Тип out_of_scope. Вопрос о договоре с самозанятым. Фрагменты — о трудовом
договоре. Ответ перечисляет условия трудового договора как условия договора с самозанятым.
{"faithfulness": 0, "answer_relevance": 0, "context_precision": 0, "comment": "Во фрагментах нет сведений о самозанятых, ответ выдаёт нормы о трудовом договоре за ответ — галлюцинация вместо отказа; context_precision для out_of_scope не определён."}

Образец 3. Тип out_of_scope. Вопрос о ставке налога. Фрагменты — о заработной плате.
Ответ: «В базе знаний нет ответа на этот вопрос.»
{"faithfulness": 1, "answer_relevance": 1, "context_precision": 0, "comment": "Ответ — корректный отказ, совпадает с эталоном, ничего не добавляет; context_precision для out_of_scope не определён."}
"""

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


# Модель судьи может отличаться от генератора: OLLAMA_JUDGE_MODEL в .env.
# run() создаёт судью той же фабрикой build_client(), что и генератор, поэтому
# модель подменяется здесь, на копии клиента судьи; генератор не затрагивается.
JUDGE_MODEL = os.getenv("OLLAMA_JUDGE_MODEL", "")


def judge_client(llm):
    """Клиент судьи: копия переданного с моделью OLLAMA_JUDGE_MODEL, если задана."""
    if JUDGE_MODEL and hasattr(llm, "model") and hasattr(llm, "host"):
        llm = copy.copy(llm)
        llm.model = JUDGE_MODEL
    return llm


def _zero(comment: str) -> dict:
    return {"faithfulness": 0.0, "answer_relevance": 0.0,
            "context_precision": 0.0, "comment": comment}


def _score(value) -> float:
    """Число от 0 до 1; судьи иногда пишут «0,8» строкой."""
    number = float(str(value).replace(",", ".").strip())
    return min(1.0, max(0.0, number))


def parse_verdict(raw: str) -> dict:
    """Снять обрамление, вырезать JSON-объект и разобрать три оценки."""
    text = _FENCE_RE.sub("", raw.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("в ответе судьи нет JSON-объекта")
    data = json.loads(text[start:end + 1])
    missing = [m for m in METRICS if m not in data]
    if missing:
        raise ValueError(f"нет полей {missing}")
    verdict = {m: _score(data[m]) for m in METRICS}
    verdict["comment"] = str(data.get("comment", "")).strip()
    return verdict


def judge(llm, question: str, answer: str, expected: str,
          chunks: list[Chunk] | None = None,
          case_type: str = "direct") -> dict:
    """Оценить один ответ.

    Параметры:
        llm       — LLMClient, через который вызывается судья.
        question  — текст вопроса.
        answer    — ответ системы.
        expected  — эталонный ответ.
        chunks    — фактические фрагменты, ушедшие в модель (list[Chunk]).
                    Без них context_precision не измеряется, а faithfulness
                    проверяет сходство с эталоном, а не верность источнику.
                    Подавайте их в промпт через prompts.format_chunks().
        case_type — тип вопроса: "direct" | "cross_section" | "ambiguous"
                    | "out_of_scope". Для out_of_scope context_precision
                    не определён — верните 0.0 и отметьте это в комментарии.

    Возвращает {"faithfulness": float, "answer_relevance": float,
                "context_precision": float, "comment": str}.
    На сбое вызова или разбора — нули и comment с префиксом "разбор:" или "сбой".

    Подсказка: модели любят обрамлять JSON в ```json ... ``` — снимите обрамление
    перед json.loads(), иначе разбор упадёт на первом же вопросе.
    """
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": (
            f"Тип вопроса: {case_type}\n\n"
            f"Вопрос: {question}\n\n"
            f"Эталонный ответ: {expected or '(не задан)'}\n\n"
            f"Фрагменты, которые получила система:\n{format_chunks(chunks or [])}\n\n"
            f"Ответ системы: {answer}\n\n"
            "Верни JSON с тремя оценками."
        )},
    ]
    try:
        raw = judge_client(llm).generate(messages, temperature=0.0)
    except Exception as exc:  # клиент обязан не бросать, но прогон важнее
        return _zero(f"сбой вызова судьи: {type(exc).__name__}: {exc}")
    if raw.startswith("Ошибка LLM"):
        return _zero(f"сбой вызова судьи: {raw}")

    try:
        verdict = parse_verdict(raw)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return _zero(f"разбор: {exc}; ответ судьи: {raw[:200]!r}")

    if case_type == "out_of_scope":
        verdict["context_precision"] = 0.0
        if "не определ" not in verdict["comment"]:
            verdict["comment"] += " (context_precision для out_of_scope не определён)"
    return verdict


def run() -> None:
    """Прогнать датасет и напечатать отчёт. Готово, менять не нужно.

    In-scope и out_of_scope считаются раздельно: у out_of_scope context_precision
    не определён, а hit-rate@5 по ним не считается (см. evaluate_retrieval в
    ingest.py). Сбои разбора судьи не включаются в средние: один нечитаемый
    ответ не должен ронять среднюю оценку без изменения системы.
    """
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    if len(cases) != 10:
        print(f"ВНИМАНИЕ: в датасете {len(cases)} вопросов, нужно 10.\n")

    orchestrator = Orchestrator()
    judge_llm = build_client()

    in_scope = {"faithfulness": [], "answer_relevance": [], "context_precision": []}
    oop = {"faithfulness": [], "answer_relevance": [], "refusals": 0}
    parse_failures: list[int] = []

    print(f"Судья: {type(judge_llm).__name__}; "
          f"генератор: {type(orchestrator.llm).__name__}\n")

    try:
        for case in cases:
            # Своя сессия на вопрос: иначе память от прошлого вопроса исказит ответ
            session_id = f"test:{uuid.uuid4()}"
            answer = orchestrator.handle(session_id, case["question"])
            chunks = orchestrator._context.get(session_id, [])
            case_type = case.get("type", "direct")
            verdict = judge(judge_llm, case["question"], answer,
                            case.get("expected", ""), chunks, case_type)

            f = float(verdict.get("faithfulness", 0.0))
            ar = float(verdict.get("answer_relevance", 0.0))
            cp = float(verdict.get("context_precision", 0.0))
            comment = str(verdict.get("comment", ""))
            broken = comment.startswith(("разбор:", "сбой"))
            if broken:
                parse_failures.append(case["id"])

            if case_type == "out_of_scope":
                if not broken:
                    oop["faithfulness"].append(f)
                    oop["answer_relevance"].append(ar)
                    if f >= 0.5 and ar >= 0.5:
                        oop["refusals"] += 1
            else:
                if not broken:
                    in_scope["faithfulness"].append(f)
                    in_scope["answer_relevance"].append(ar)
                    in_scope["context_precision"].append(cp)

            flag = "ГАЛЛЮЦИНАЦИЯ" if (not broken and f < 0.5) else "ok"
            print(f"[{case['id']:>2}] {flag:<13} {case_type:<13} "
                  f"F={f:.2f} AR={ar:.2f} CP={cp:.2f}"
                  f"{'  СБОЙ РАЗБОРА' if broken else ''}")
            print(f"     вопрос: {case['question']}")
            print(f"     ответ:  {answer[:160]}")
            print(f"     судья:  {comment}\n")
    finally:
        orchestrator.close()

    print("--- Сводка ---")
    for metric, values in in_scope.items():
        if values:
            print(f"in-scope {metric:<18} {statistics.mean(values):.2f}  "
                  f"(n={len(values)})")
    if oop["faithfulness"]:
        print(f"out_of_scope F-среднее   {statistics.mean(oop['faithfulness']):.2f}")
        print(f"out_of_scope AR-среднее  {statistics.mean(oop['answer_relevance']):.2f}")
        print(f"out_of_scope отказов     {oop['refusals']}/{len(oop['faithfulness'])}")
    hallucinations = sum(1 for v in in_scope["faithfulness"] if v < 0.5)
    print(f"Галлюцинаций (F<0.5)     {hallucinations} "
          f"из {len(in_scope['faithfulness'])}")
    if parse_failures:
        print(f"Сбоев разбора: {len(parse_failures)} (id: {parse_failures})")


if __name__ == "__main__":
    run()
