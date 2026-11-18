"""check_graph.py — проверка графа ролей (П3).

Граф должен быть связным: из INITIAL_ROLE достижима каждая роль, у каждой
роли есть выход, а хотя бы один переход запрещён. Скрипт не поднимает
MCP и не ходит в LLM — читает только объявления класса.
"""

import sys

from orchestrator import Orchestrator


def main() -> int:
    roles = set(Orchestrator.ROLES)
    graph = Orchestrator.TRANSITIONS
    intents = Orchestrator.INTENT_TO_ROLE
    errors: list[str] = []

    missing = roles - set(graph)
    if missing:
        errors.append(f"в TRANSITIONS нет ролей: {sorted(missing)}")
    extra = set(graph) - roles
    if extra:
        errors.append(f"в TRANSITIONS роли, которых нет в ROLES: {sorted(extra)}")

    destinations = {dst for edges in graph.values() for dst in edges}
    unknown = destinations - roles
    if unknown:
        errors.append(f"переходы в неизвестные роли: {sorted(unknown)}")

    for role in sorted(roles):
        if not graph.get(role):
            errors.append(f"из роли {role} нет выхода")

    # Обход в ширину от точки входа: каждая роль должна быть достижима.
    seen = {Orchestrator.INITIAL_ROLE}
    queue = [Orchestrator.INITIAL_ROLE]
    while queue:
        current = queue.pop()
        for nxt in graph.get(current, set()):
            if nxt in roles and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    unreachable = roles - seen
    if unreachable:
        errors.append(f"недостижимые роли: {sorted(unreachable)}")

    # Запрещённый переход — это пара ролей, которой нет в графе.
    forbidden = [
        (src, dst)
        for src in sorted(roles)
        for dst in sorted(roles)
        if dst not in graph.get(src, set())
    ]
    if not forbidden:
        errors.append("нет ни одного запрещённого перехода")

    if Orchestrator.INITIAL_ROLE not in roles:
        errors.append(f"INITIAL_ROLE={Orchestrator.INITIAL_ROLE!r} нет в ROLES")
    if Orchestrator.FALLBACK_ROLE not in roles:
        errors.append(f"FALLBACK_ROLE={Orchestrator.FALLBACK_ROLE!r} нет в ROLES")

    unknown_targets = set(intents.values()) - roles
    if unknown_targets:
        errors.append(f"интенты ведут в неизвестные роли: {sorted(unknown_targets)}")
    unused = roles - set(intents.values())
    if unused:
        errors.append(f"роли без своего интента: {sorted(unused)}")

    print("Роли:", ", ".join(sorted(roles)))
    print("Переходы:")
    for role in sorted(graph):
        edges = ", ".join(sorted(graph[role])) or "—"
        print(f"  {role} -> {edges}")
    if forbidden:
        print(f"Запрещено, например: {forbidden[0][0]} -> {forbidden[0][1]}")

    if errors:
        print("\nОШИБКИ:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nOK: роли достижимы, у каждой есть выход, запрещённый переход есть.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
