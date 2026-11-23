"""check_memory.py — проверки памяти П5 (шаги 3.1–3.4 и изоляция сессий).

Модель и MCP не нужны: проверяется только memory.py на SQLite.
Запуск: python check_memory.py
"""

import subprocess
import sys

import config
import memory


def schema() -> None:
    memory.create_schema()
    assert config.DB_PATH.exists(), "Файл БД не создан"
    print(f"БД создана: {config.DB_PATH}")
    memory.create_schema()
    print("Повторный вызов create_schema не упал — ок")
    conn = memory.connect()
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchall()
    finally:
        conn.close()
    assert tables, "Таблица messages не создана"
    print("Таблица messages существует — ок")


def save_and_clear() -> None:
    memory.clear("test")
    memory.save("test", "user", "реплика 1")
    memory.save("test", "assistant", "ответ 1")
    conn = memory.connect()
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", ("test",)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("user", "реплика 1"), ("assistant", "ответ 1")], rows
    print("save: реплики записаны в правильном порядке — ок")
    memory.clear("test")
    assert memory.window("test", 10) == [], "clear не удалил реплики"
    print("clear: реплики сессии удалены — ок")


def window() -> None:
    memory.clear("t1")
    for i in range(1, 11):
        memory.save("t1", "user" if i % 2 else "assistant", f"сообщение {i}")
    w = memory.window("t1", 4)
    print([m.content for m in w])
    assert [m.content for m in w] == [
        "сообщение 7", "сообщение 8", "сообщение 9", "сообщение 10"
    ], "Окно вернуло не те сообщения или не в том порядке"
    print("окно: 4 последних в хронологическом порядке — ок")
    memory.clear("t1")


def restart() -> None:
    subprocess.run([sys.executable, "-c", """
import memory
memory.create_schema()
memory.clear("t2")
for i in range(1, 6):
    memory.save("t2", "user", f"реплика {i}")
print("записано 5 реплик")
"""], check=True)
    subprocess.run([sys.executable, "-c", """
import memory
w = memory.window("t2", 10)
print([m.content for m in w])
assert len(w) == 5, "история не пережила перезапуск"
assert [m.content for m in w] == [f"реплика {i}" for i in range(1, 6)]
print("перезапуск: история на месте — ок")
memory.clear("t2")
"""], check=True)


def isolation() -> None:
    memory.clear("alice")
    memory.clear("bob")
    memory.save("alice", "user", "мой оклад 80 000")
    memory.save("bob", "user", "вопрос про отпуск")
    a = [m.content for m in memory.window("alice", 10)]
    b = [m.content for m in memory.window("bob", 10)]
    print("alice:", a, "| bob:", b)
    assert a == ["мой оклад 80 000"] and b == ["вопрос про отпуск"], "сессии видят чужие реплики"
    memory.clear("alice")
    assert [m.content for m in memory.window("bob", 10)] == ["вопрос про отпуск"], \
        "clear одной сессии задел другую"
    print("изоляция: сессии не видят реплик друг друга, clear не трогает чужую — ок")
    memory.clear("bob")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # иначе вывод подпроцессов обгоняет свой
    for step in (schema, save_and_clear, window, restart, isolation):
        print(f"--- {step.__name__} ---")
        step()
    print("\nOK: все проверки памяти пройдены.")
