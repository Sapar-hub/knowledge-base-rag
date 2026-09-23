#!/usr/bin/env python3
"""Сборщик источников для П1/П2.

Воспроизводит data/raw/tk_rf.md, data/raw/post_858.md и data/raw/rost_guide.md
из открытых страниц consultant.ru. Только стандартная библиотека, новых
зависимостей нет. Повторные прогоны используют локальный кеш.

Запуск:
    python3 data/fetch_sources.py                  # собрать все три файла
    python3 data/fetch_sources.py --only tk        # только ТК РФ
    python3 data/fetch_sources.py --cache DIR      # переиспользовать готовый кеш
"""

import argparse
import html as htmlmod
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PREFIXES = ("Часть ", "Раздел ", "Глава ")
TK_DOC = "/document/cons_doc_LAW_34683"
# Сквозное оглавление руководства: единственная страница в наборе LAW_440271,
# которая дублирует все 323 раздела списком; по отдельности разделы ниже есть.
SKIP_APPENDIX_440271 = "ad1aa87e6a209595b4c88e4974676fa63b66cf02"

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
         "VIII": 8, "IX": 9, "X": 10, "XI": 11}
COMMENT = re.compile(
    r"^(См\.\s|Открыть\s|Закрыть\s|\(см\.\s|\(в ред\.\s|КонсультантПлюс|"
    r"Распечатать|В избранное|Сравнить с)")

TK_HEADER = [
    "Трудовой кодекс Российской Федерации от 30 декабря 2001 года N 197-ФЗ",
    "Актуальная редакция (консолидированный текст по данным КонсультантПлюс),",
    "структура: Раздел / Глава / Статья.",
]
ROST_HEADER = [
    "# Руководство по соблюдению обязательных требований трудового законодательства",
    "(утв. Приказом Роструда от 11.11.2022 N 253)",
]


def fetch(cache: Path, url: str) -> str:
    """Скачать страницу (с кешем по последнему сегменту URL)."""
    key = url.rstrip("/").rsplit("/", 1)[-1] + ".html"
    fp = cache / key
    if fp.exists():
        return fp.read_text(encoding="utf-8", errors="ignore")
    req = Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.9"})
    for attempt in range(4):
        try:
            with urlopen(req, timeout=30) as r:
                data = r.read()
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data)
            time.sleep(0.35)  # вежливость — только при реальной загрузке
            return data.decode("utf-8", errors="ignore")
        except HTTPError as e:
            if e.code == 404:
                return ""
            time.sleep(2)
        except URLError:
            time.sleep(2)
        time.sleep(1.5)
    return ""


def strip_insert_divs(h: str) -> str:
    """Вырезать сбалансированные блоки <div class="...doc-insert..."> (пояснения КонсультантПлюс)."""
    out, i, n = [], 0, len(h)
    start_pat = re.compile(r'<div[^>]*class="[^"]*doc-insert[^"]*"[^>]*>')
    div_re = re.compile(r"<div\b[^>]*>|</div>")
    while i < n:
        m = start_pat.search(h, i)
        if not m:
            out.append(h[i:])
            break
        out.append(h[i:m.start()])
        k, depth = m.end(), 1
        while k < n and depth > 0:
            mm = div_re.search(h, k)
            if not mm:
                break
            depth += 1 if mm.group(0).startswith("<div") else -1
            k = mm.end()
        i = k
    return "".join(out)


def to_plain(h: str) -> str:
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S)
    h = re.sub(r"<style.*?</style>", " ", h, flags=re.S)
    h = re.sub(r"<br[^>]*>", "\n", h)
    h = re.sub(r"</(p|h1|h2|h3|h4|h5|li|div|table|tr)>", "\n", h)
    h = re.sub(r"<td[^>]*>", " ", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = htmlmod.unescape(h)
    h = re.sub(r"[ \t\u00a0]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    h = re.sub(r"^[ \t]+", "", h, flags=re.M)
    h = re.sub(r"[ \t]+$", "", h, flags=re.M)
    return h.strip("\n")


def document_page(cache: Path, url: str) -> str:
    page = fetch(cache, url)
    if not page:
        return ""
    m = re.search(
        r'<div class="document-page__content[^"]*">(.*?)</div>\s*'
        r'(?:<div class="full-text|<div class="external-block__bottom|$)',
        page, flags=re.S)
    if not m:
        m = re.search(r'<div class="document-page__content[^"]*">(.*)', page, flags=re.S)
    if not m:
        return ""
    return to_plain(strip_insert_divs(m.group(1)))


def sub_pages(cache: Path, doc: str) -> list:
    """Порядок подстраниц документа из его корневой страницы."""
    root = fetch(cache, "https://www.consultant.ru/document/" + doc + "/")
    hrefs = re.findall(r'href="/document/' + doc + r'/([0-9a-f]{40})/"', root)
    return list(dict.fromkeys(hrefs))


# ----------------------------------------------------------------------------- ТК РФ

def parse_toc(html_src: str):
    records, stack, depth = [], {}, 0
    pat = re.compile(
        r"<ul[^>]*>|</ul>|<a[^>]*href=\"(/document/cons_doc_LAW_34683/[0-9a-f]+/)\"[^>]*>(.*?)</a>|</li>",
        re.S)
    for m in pat.finditer(html_src):
        tok = m.group(0)
        if tok.startswith("<ul"):
            depth += 1
        elif tok.startswith("</ul>"):
            depth = max(0, depth - 1)
        elif tok.startswith("</li>"):
            continue
        else:
            href, txt = m.group(1), m.group(2)
            text = htmlmod.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
            if text.startswith(PREFIXES):
                stack[depth] = text
                for k in [k for k in stack if k > depth]:
                    del stack[k]
            elif text.startswith("Статья "):
                records.append((dict(stack), text, href))
    return records


def tk_article(cache: Path, url: str):
    h = fetch(cache, url)
    h = strip_insert_divs(h)
    title_m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    title = htmlmod.unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip() if title_m else ""
    title = re.sub(r"^\s*ТК РФ\s*", "", title).strip()
    parts = []
    for tag, txt in re.findall(r"<(h1|h2|h3|p)[^>]*>(.*?)</\1>", h, re.S):
        t = htmlmod.unescape(re.sub(r"<[^>]+>", "", txt))
        t = re.sub(r"[\u00a0\u200b\u2009]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            parts.append(t)
    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(ln for ln in text.split("\n")
                     if not re.match(r"^ТК РФ Статья \d", ln.strip())
                     and not re.match(r"^Ст\. ?\d[\d.]* ТК РФ", ln.strip()))
    return title, text


def fmt_tk(text: str) -> str:
    """Привести структуру к эталону: пустые строки допустимы после заголовков
    и перед структурными заголовками (#/##/###/####), но не между телом статьи
    и следующим заголовком '#### Статья ...'. Коллапс двойных пустых строк."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    parts = text.split("\n")
    res = []
    for idx, ln in enumerate(parts):
        if ln == "" and idx + 1 < len(parts) and parts[idx + 1].startswith("#### "):
            k = idx - 1
            while k >= 0 and parts[k] == "":
                k -= 1
            if k >= 0 and not parts[k].startswith("#"):
                continue
        res.append(ln)
    return "\n".join(res)


def build_tk(cache: Path, out: Path):
    toc = fetch(cache, "https://www.consultant.ru" + TK_DOC + "/")
    records = parse_toc(toc)
    uniq = {}
    for path, title, href in records:
        uniq.setdefault(href, (title, path))
    lines = list(TK_HEADER)
    cur_part = cur_sec = cur_ch = None
    for href, (title, path) in uniq.items():
        def pick(prefix):
            for d in sorted(path):
                if path[d].startswith(prefix):
                    return path[d]
            return None
        part, sec, ch = pick("Часть "), pick("Раздел "), pick("Глава ")
        if part != cur_part:
            lines.append(f"\n# {part}")
            cur_part, cur_sec, cur_ch = part, None, None
        if sec != cur_sec:
            lines.append(f"\n## {sec}")
            cur_sec, cur_ch = sec, None
        if ch != cur_ch:
            lines.append(f"\n### {ch}")
            cur_ch = ch
        art_title, art_text = tk_article(cache, "https://www.consultant.ru" + href)
        lines.append(f"\n#### {art_title}\n")
        lines.append(art_text)
        if len(lines) % 200 == 0:
            print(f"  ... {len(lines)} статей обработано")
    write_md(out / "tk_rf.md", fmt_tk("\n".join(lines)))
    print(f"tk_rf.md: {len(uniq)} статей, {len(lines)} строк")


# -------------------------------------------------------------------------- ПОСТАНОВЛЕНИЕ 858

def build_858(cache: Path, out: Path):
    pages = sub_pages(cache, "cons_doc_LAW_204023")
    parsed = []  # (kind, rank, heading, body)  kind: 'dec'|'form'|'sec'
    for href in pages:
        text = document_page(cache, "https://www.consultant.ru/document/cons_doc_LAW_204023/" + href)
        if not text.strip():
            continue
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        lines = [ln for ln in lines if not COMMENT.match(ln)]
        if not lines:
            continue
        if re.match(r"^ПРАВИТЕЛЬСТВО РОССИЙСКОЙ ФЕДЕРАЦИИ$", lines[0]):
            lines = [ln for ln in lines if not re.match(
                r"^(Список изменяющих документов|от (15\.11\.2019|10\.07\.2020|19\.04\.2021)|\(в ред\.)", ln)]
            parsed.append(("dec", 0, None, "\n".join(lines)))
        elif re.match(r"^[IVX]+\.\s+\S", lines[0]):
            rank = ROMAN.get(re.match(r"^([IVX]+)\.", lines[0]).group(1), 99)
            parsed.append(("sec", rank, lines[0], "\n".join(lines[1:])))
        else:
            body = "\n".join(ln for ln in lines if not re.match(r"^[IVX]+\.\s+\S", ln))
            parsed.append(("form", 1, "Типовая форма трудового договора", body))
    parsed.sort(key=lambda x: x[1])
    blocks = []
    for kind, _, heading, body in parsed:
        if kind == "dec":
            blocks.append(body)
        else:
            blocks.append(f"## {heading}\n\n{body}".rstrip())
    write_md(out / "post_858.md", "\n\n".join(blocks))
    print(f"post_858.md: разделов I-XI + форма = {len(blocks)} блоков")


# ------------------------------------------------------------------- РУКОВОДСТВО РОСТРУДА

def build_rost_guide(cache: Path, out: Path):
    pages = sub_pages(cache, "cons_doc_LAW_440271")
    blocks, skipped = [], 0
    for href in pages:
        if href == SKIP_APPENDIX_440271:
            skipped += 1
            continue
        text = document_page(cache, "https://www.consultant.ru/document/cons_doc_LAW_440271/" + href)
        if not text.strip():
            continue
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        lines = [ln for ln in lines if not COMMENT.match(ln)]
        if not lines:
            continue
        head = lines[0]
        body = "\n".join(lines[1:])
        if head == "МИНИСТЕРСТВО ТРУДА И СОЦИАЛЬНОЙ ЗАЩИТЫ РОССИЙСКОЙ ФЕДЕРАЦИИ":
            heading = "## Приказ Роструда от 11.11.2022 N 253"
        elif head in ("Для работников", "Для работодателей"):
            heading = f"### {head}"
        elif re.match(r"^[IVX]+\.\s+\S", head):
            heading = f"#### {head}"
        else:
            heading = f"## {head}"
        blocks.append(f"{heading}\n\n{body}".rstrip())
    write_md(out / "rost_guide.md", "\n".join(ROST_HEADER) + "\n\n" + "\n\n".join(blocks))
    print(f"rost_guide.md: {len(blocks)} разделов (пропущено оглавлений: {skipped})")


def write_md(fp: Path, text: str):
    text = re.sub(r"\n{3,}", "\n\n", text)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Сборщик данных из consultant.ru в data/raw/*.md")
    ap.add_argument("--only", choices=["tk", "post858", "rost"], default=None)
    ap.add_argument("--cache", type=Path, default=Path("data/raw/.cache"))
    ap.add_argument("--out", type=Path, default=Path("data/raw"))
    args = ap.parse_args()

    cache = args.cache
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    jobs = (["tk", "post858", "rost"] if args.only is None else [args.only])
    for job in jobs:
        if job == "tk":
            build_tk(cache, out)
        elif job == "post858":
            build_858(cache, out)
        else:
            build_rost_guide(cache, out)


if __name__ == "__main__":
    main()