import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
from datetime import date, datetime

import requests
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, MessagesState
from ddgs import DDGS

from bs4 import BeautifulSoup  

load_dotenv(Path(__file__).resolve().parent / ".env")

# --- Env ---
token = os.environ.get("OPENROUTER_API_KEY")
if not token:
    raise ValueError("OPENROUTER_API_KEY required in .env")

# OpenRouter: стандартный endpoint
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

model = os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview")


# --- Поиск и чтение сайтов (HTTP-only, без JS рендера) ---
def search_web(
    query: str,
    *,
    today_iso: Optional[str] = None,
    max_results: int = 3,
    timelimit: Optional[str] = None,
) -> str:
    """
    Поиск через DDG (DuckDuckGo). Возвращает короткие карточки результатов.

    today_iso / timelimit используются, чтобы уменьшать вероятность выдачи устаревшей информации.
    """
    q = query.strip()
    if today_iso:
        year = today_iso[:4]
        # Небольшое улучшение актуальности через year-ключевое слово.
        if year and year not in q:
            q = f"{q} {year}"
    print(f"   [🔍]: {q}")
    try:
        with DDGS() as ddgs:
            kwargs: Dict[str, Any] = {"max_results": max_results}
            if timelimit:
                kwargs["timelimit"] = timelimit
            results = list(ddgs.text(q, **kwargs))
        if not results:
            return "Nothing found. Try rephrasing your query."
        return "\n\n".join(
            f"Title: {r.get('title','')}\nText: {r.get('body','')}\nSource: {r.get('href','')}"
            for r in results
        )
    except Exception as e:
        return f"Search error: {e}. Try a different approach."


def fetch_url(url: str, *, timeout: int = 25, max_bytes: int = 400_000) -> str:
    """Открывает URL и возвращает сырое HTML/текст (HTTP-only)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; travel-agent/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.7",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        raise RuntimeError(f"HTTP GET {url} failed: {e}") from e

    # Many travel sites (TripAdvisor, etc.) block direct scraping with Cloudflare 403.
    # As a pragmatic fallback we fetch via a server-side text proxy (Jina).
    if r.status_code == 403:
        jina_url = f"https://r.jina.ai/{url}"
        try:
            r2 = requests.get(jina_url, headers=headers, timeout=timeout, allow_redirects=True)
            if r2.ok:
                txt = r2.text or ""
                # If Jina couldn't access the target (still blocked), it returns a short warning.
                # Treat it as "no content" so downstream doesn't learn from that warning text.
                if "Target URL returned error" in txt:
                    return ""
                return txt[:max_bytes]
        except requests.RequestException:
            return ""

        # Jina request itself failed or returned non-ok status.
        return ""

    if not r.ok:
        raise RuntimeError(f"HTTP GET {url} failed: {r.status_code}: {r.text[:200]}")

    # requests.text декодирует; но ограничим общий объем.
    txt = r.text or ""
    return txt[:max_bytes]


def extract_visible_text(html: str, *, max_chars: int = 10000) -> str:
    """Извлекает видимый текст из HTML. Пытаемся использовать BeautifulSoup, иначе fallback."""
    if not html:
        return ""
    if BeautifulSoup is None:
        # Fallback без bs4: грубо убираем теги.
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
        try:
            tag.decompose()
        except Exception:
            pass
    # Убираем очевидный мусор.
    for tag in soup(["header", "footer", "nav", "aside"]):
        try:
            tag.decompose()
        except Exception:
            pass
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def truncate_text(text: str, max_chars: int = 2500) -> str:
    """Ограничение длины для безопасной отправки в LLM."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 10] + "...(truncated)..."


def _save_sources_json(data: Dict[str, Any], *, filename: str) -> Optional[str]:
    """Сохраняет собранные источники рядом с этим файлом (JSON)."""
    try:
        out_path = Path(__file__).resolve().parent / filename
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(out_path)
    except Exception as e:
        print(f"[warn] Failed to save sources JSON: {e}")
        return None


def safe_json_extract(text: str) -> Optional[Any]:
    """
    Пытается извлечь первый JSON-объект из строки и распарсить его.
    Используется в местах, где LLM должен вернуть строго JSON.
    """
    if not text:
        return None
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _extract_links_from_search(search_result: str, *, max_links: int = 5) -> List[str]:
    """Пытается вытащить href из результата search_web."""
    hrefs: List[str] = []
    for line in search_result.splitlines():
        if line.strip().startswith("Source:"):
            u = line.split("Source:", 1)[1].strip()
            if u and u not in hrefs:
                hrefs.append(u)
        if len(hrefs) >= max_links:
            break
    return hrefs


# --- Travel: каркасы субагентов и координатора (реализация в следующих to-do) ---
def _dedup_seed_queries(queries: List[str], *, max_seed: int = 4) -> List[str]:
    """Дедупликация + нормализация пробелов (как в 3 исходных сабагентах)."""
    seen_q = set()
    seed_queries: List[str] = []
    for q in queries:
        q2 = re.sub(r"\s+", " ", q).strip()
        if q2 and q2 not in seen_q:
            seed_queries.append(q2)
            seen_q.add(q2)
            if len(seed_queries) >= max_seed:
                break
    return seed_queries


def _run_travel_web_subagent(
    *,
    tag: str,
    today_iso: str,
    travel_profile: Dict[str, Any],
    focus_claims: Optional[List[str]],
    seed_queries: List[str],
    existing_pages: Optional[List[Dict[str, str]]] = None,
    existing_queries_used: Optional[List[str]] = None,
    search_timelimit: Optional[str],
    max_pages: int,
    max_rounds: int,
    extract_max_chars: int,
    min_text_len: int,
    excerpt_max_chars: int,
    decide_body: str,
    summary_prompt_builder,
    pages_text_take: int,
) -> Dict[str, Any]:
    """
    Унифицированный runner для 3 travel-сабагентов:
    - несколько поисковых раундов (LLM решает "need_more" и "next_query")
    - fetch/extract по top hrefs
    - финальный summary через LLM
    """

    pages: List[Dict[str, str]] = list(existing_pages or [])
    queries_used: List[str] = list(existing_queries_used or [])

    existing_urls = {p.get("url") for p in pages if p.get("url")}
    existing_query_set = set(queries_used)

    queries_queue = list(seed_queries)
    # Не тратим попытки на запросы, которые уже использовались в прошлом проходе.
    queries_queue = [q for q in queries_queue if q not in existing_query_set]
    current_query = queries_queue.pop(0) if queries_queue else None

    round_idx = 0
    while current_query and round_idx < max_rounds and len(pages) < max_pages:
        print(f"[TRAVEL-{tag}] Round {round_idx+1}/{max_rounds} search query: {current_query}")
        queries_used.append(current_query)

        srch = search_web(
            current_query,
            today_iso=today_iso,
            max_results=3,
            timelimit=search_timelimit,
        )
        hrefs = _extract_links_from_search(srch, max_links=3)
        print(f"[TRAVEL-{tag}] Round {round_idx+1} hrefs: {hrefs}")

        before_count = len(pages)
        for href in hrefs:
            if len(pages) >= max_pages:
                break
            if href in existing_urls:
                continue
            try:
                html = fetch_url(href)
                text = extract_visible_text(html, max_chars=extract_max_chars)
                if not text or len(text) < min_text_len:
                    continue
                pages.append({"url": href, "excerpt": truncate_text(text, max_chars=excerpt_max_chars)})
                existing_urls.add(href)
            except Exception as e:
                print(f"   [warn] fetch/extract failed for {href}: {e}")
                continue

        round_pages = pages[before_count:]
        round_pages_text = "\n\n".join(
            [f"URL: {p['url']}\nTEXT:\n{p['excerpt']}" for p in round_pages[:6]]
        )
        print(f"[TRAVEL-{tag}] Round {round_idx+1} extracted pages: {len(round_pages)}")

        # Ограничиваем размер существующего доказательства, чтобы промпт не раздувался.
        existing_evidence_text = "\n\n".join(
            [
                f"URL: {p.get('url','')}\nTEXT:\n{truncate_text(p.get('excerpt','') or '', max_chars=1200)}"
                for p in pages[:4]
                if p.get("url")
            ]
        )

        decide_prompt = f"""You are deciding whether to perform one more web search round
{decide_body}

Existing evidence (do not re-fetch/duplicate): 
{existing_evidence_text if existing_evidence_text else 'NO_EXISTING_EVIDENCE'}

Return ONLY valid JSON:
{{
  "need_more": true/false,
  "next_query": string|null
}}

travel_profile: {json.dumps(travel_profile, ensure_ascii=False)}
today_iso: {today_iso}
focus_claims: {json.dumps(focus_claims or [], ensure_ascii=False)}
current_query: {current_query}
round_excerpts:
{round_pages_text if round_pages_text else 'NO_PAGES_FROM_ROUND'}
"""
        raw_decision = llm.invoke([HumanMessage(content=decide_prompt)]).content
        decision = safe_json_extract(raw_decision) or {}
        need_more = bool(decision.get("need_more", False))
        next_query = decision.get("next_query")

        print(f"[TRAVEL-{tag}] Round {round_idx+1} decision raw: {truncate_text(raw_decision, 2500)}")
        print(f"[TRAVEL-{tag}] Round {round_idx+1} need_more={need_more} next_query={next_query}")

        if not need_more:
            break

        if isinstance(next_query, str) and next_query.strip():
            q2 = next_query.strip()
            if q2 not in existing_query_set and q2 not in set(queries_used):
                current_query = q2
            else:
                # Если модель повторяет уже использованный запрос — возьмем из очереди,
                # а если там пусто, завершим цикл (иначе рискуем зациклиться).
                current_query = queries_queue.pop(0) if queries_queue else None
        else:
            current_query = queries_queue.pop(0) if queries_queue else None

        round_idx += 1

    pages_text = "\n\n".join(
        [f"URL: {p['url']}\nTEXT:\n{p['excerpt']}" for p in pages[:pages_text_take]]
    )
    prompt = summary_prompt_builder(pages_text)
    summary = llm.invoke(
        [
            SystemMessage(content="You answer in Russian if input is Russian."),
            HumanMessage(content=prompt),
        ]
    ).content
    return {"seed_queries": seed_queries, "queries_used": queries_used, "pages": pages, "summary": summary}


def official_docs_subagent(
    *,
    today_iso: str,
    travel_profile: Dict[str, Any],
    focus_claims: Optional[List[str]] = None,
    existing_material: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Подагент 1: официальные документы/данные.
    Возвращает сырой материал: ссылки + извлеченный текст/фрагменты.
    """
    destination = travel_profile.get("destination") or travel_profile.get("country") or ""
    passport_country = (
        travel_profile.get("passportCountry")
        or travel_profile.get("nationality")
        or travel_profile.get("citizenship")
        or ""
    )
    purpose = travel_profile.get("purpose") or ""
    year = today_iso[:4] if today_iso else ""

    base_queries: List[str] = []
    if destination and passport_country:
        base_queries.append(f"{destination} entry requirements {passport_country} {year}".strip())
        base_queries.append(f"{destination} visa requirements {passport_country} official {year}".strip())
    if destination:
        base_queries.append(f"{destination} official tourism entry rules {year}".strip())
    if destination and purpose:
        base_queries.append(f"{destination} {purpose} entry requirements official {year}".strip())

    if focus_claims:
        for c in focus_claims[:6]:
            base_queries.append(f"{c} {destination} official {year}".strip())

    seed_queries = _dedup_seed_queries(base_queries, max_seed=4)

    decide_body = """for collecting OFFICIAL travel-entry information.

Given travel_profile and focus_claims (optional) and the excerpts extracted for the current query round,
decide if you already have enough authoritative evidence for:
- entry/visa/document requirements
- any key warnings that materially affect the traveler"""

    def summary_prompt_builder(pages_text: str) -> str:
        return f"""You are Travel official-data extractor.

Given travel_profile and web-extracted page excerpts, create a structured summary of official/authoritative rules that matter for the traveler.

Rules:
- Only use facts that are supported by the excerpts; if something is not supported, mark it as "UNCONFIRMED".
- Output format (exact headings):
  1) "Requirements" as a bullet list of traveler-impacting rules.
  2) "Warnings" as a bullet list (if any).
  3) "Unconfirmed" as a bullet list (if any).
  4) "Sources" as a bullet list of URLs.
- If focus_claims provided: prioritize them; map each focus_claim to whether it's supported/unconfirmed.

travel_profile (JSON): {json.dumps(travel_profile, ensure_ascii=False)}
today_iso: {today_iso}
focus_claims: {json.dumps(focus_claims or [], ensure_ascii=False)}

EXCERPTS:
{pages_text if pages_text else 'NO_PAGES'}"""

    return _run_travel_web_subagent(
        tag="OFFICIAL",
        today_iso=today_iso,
        travel_profile=travel_profile,
        focus_claims=focus_claims,
        seed_queries=seed_queries,
        existing_pages=(existing_material or {}).get("pages"),
        existing_queries_used=(existing_material or {}).get("queries_used"),
        search_timelimit="y",
        max_pages=6,
        max_rounds=2,
        extract_max_chars=3000,
        min_text_len=200,
        excerpt_max_chars=10000,
        decide_body=decide_body,
        summary_prompt_builder=summary_prompt_builder,
        pages_text_take=6,
    )


def reviews_subagent(
    *,
    today_iso: str,
    travel_profile: Dict[str, Any],
    focus_claims: Optional[List[str]] = None,
    existing_material: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Подагент 2: свежие отзывы людей (real-world)."""
    destination = travel_profile.get("destination") or travel_profile.get("country") or ""
    passport_country = (
        travel_profile.get("passportCountry")
        or travel_profile.get("nationality")
        or travel_profile.get("citizenship")
        or ""
    )
    year = today_iso[:4] if today_iso else ""

    base_queries: List[str] = []
    if destination:
        base_queries.append(f"{destination} recent experience entry {year} reddit forum -tripadvisor".strip())
        base_queries.append(
            f"{destination} {passport_country} review entry {year} reddit forum -tripadvisor".strip()
            if passport_country
            else f"{destination} entry experience review {year} reddit forum -tripadvisor".strip()
        )
        base_queries.append(f"{destination} immigration queue experience {year} reddit -tripadvisor".strip())

    if focus_claims:
        for c in focus_claims[:6]:
            base_queries.append(f"{c} {destination} real experience {year} reddit forum -tripadvisor".strip())

    seed_queries = _dedup_seed_queries(base_queries, max_seed=4)

    decide_body = """for collecting REAL-WORLD travel experience (reviews).

Given travel_profile, focus_claims (optional) and excerpts extracted for the current query round,
decide if you already have enough evidence for:
- what people actually experienced (entry/queues/costs/process)
- freshness signals (when possible)
- recurring issues likely to affect the traveler"""

    def summary_prompt_builder(pages_text: str) -> str:
        return f"""You are Travel reviews analyst.

Use provided excerpts from real traveler discussions/blogs (not official websites unless explicitly stated).
Extract what people report in practice: themes, friction points, timing, costs, and whether rules were enforced differently.

Rules:
- Use only what is supported by excerpts; if uncertain, write "UNCONFIRMED".
- Output exact headings:
  1) "WhatPeopleReport" bullet list (themes, concrete events)
  2) "ContradictionsVsOfficial" bullet list (if you can infer conflicts, else empty)
  3) "Freshness" bullet list with any date signals you can find (else UNCONFIRMED)
  4) "Sources" bullet list of URLs
- Focus claims: prioritize them if provided.

travel_profile (JSON): {json.dumps(travel_profile, ensure_ascii=False)}
today_iso: {today_iso}
focus_claims: {json.dumps(focus_claims or [], ensure_ascii=False)}

EXCERPTS:
{pages_text if pages_text else 'NO_PAGES'}"""

    return _run_travel_web_subagent(
        tag="REVIEWS",
        today_iso=today_iso,
        travel_profile=travel_profile,
        focus_claims=focus_claims,
        seed_queries=seed_queries,
        existing_pages=(existing_material or {}).get("pages"),
        existing_queries_used=(existing_material or {}).get("queries_used"),
        search_timelimit="m",
        max_pages=6,
        max_rounds=2,
        extract_max_chars=2800,
        min_text_len=150,
        excerpt_max_chars=10000,
        decide_body=decide_body,
        summary_prompt_builder=summary_prompt_builder,
        pages_text_take=6,
    )


def practical_recs_subagent(
    *,
    today_iso: str,
    travel_profile: Dict[str, Any],
    focus_claims: Optional[List[str]] = None,
    existing_material: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Подагент 3: практическая подготовка / что купить и как."""
    destination = travel_profile.get("destination") or travel_profile.get("country") or ""
    purpose = travel_profile.get("purpose") or ""
    year = today_iso[:4] if today_iso else ""

    base_queries: List[str] = []
    if destination:
        base_queries.append(f"{destination} travel checklist what to prepare {year}".strip())
        base_queries.append(f"{destination} power bank travel adapter compatibility {year}".strip())
        base_queries.append(f"{destination} customs power bank import restrictions {year}".strip())
        base_queries.append(f"{destination} SIM/eSIM buying/activation for tourists {year}".strip())
    if purpose:
        base_queries.append(f"{destination} {purpose} packing list {year}".strip())

    if focus_claims:
        for c in focus_claims[:6]:
            base_queries.append(f"{c} {destination} buy prepare {year}".strip())

    seed_queries = _dedup_seed_queries(base_queries, max_seed=4)

    decide_body = """for collecting PRACTICAL travel preparation evidence (gear, adapters, charging, SIM, customs constraints).

Given travel_profile, focus_claims (optional), and the extracted excerpts for the current query round,
decide if you already have enough evidence to provide practical recommendations without guessing."""

    def summary_prompt_builder(pages_text: str) -> str:
        return f"""You are Travel practical recommendations assistant.

From the web excerpts, provide practical preparation advice:
- what to pack
- what devices/accessories to buy
- power/adapter/charging compatibility notes
- for items that may require compliance (e.g., power banks), separate:
  (A) "Advice": suggestions from travelers/blogs
  (B) "Requirements": rules that are stated as actual requirements/restrictions

Output exact headings:
1) "Advice" bullet list (practical tips)
2) "Requirements" bullet list (only if clearly supported; else empty)
3) "RisksAndTradeoffs" bullet list (what can go wrong, incompatibilities)
4) "Sources" bullet list of URLs

If focus_claims provided: prioritize and explicitly label each focus_claim as Advice/Requirements/UNCONFIRMED.

travel_profile (JSON): {json.dumps(travel_profile, ensure_ascii=False)}
today_iso: {today_iso}
focus_claims: {json.dumps(focus_claims or [], ensure_ascii=False)}

EXCERPTS:
{pages_text if pages_text else 'NO_PAGES'}"""

    return _run_travel_web_subagent(
        tag="PRACTICAL",
        today_iso=today_iso,
        travel_profile=travel_profile,
        focus_claims=focus_claims,
        seed_queries=seed_queries,
        existing_pages=(existing_material or {}).get("pages"),
        existing_queries_used=(existing_material or {}).get("queries_used"),
        search_timelimit="y",
        max_pages=7,
        max_rounds=2,
        extract_max_chars=2600,
        min_text_len=150,
        excerpt_max_chars=20000,
        decide_body=decide_body,
        summary_prompt_builder=summary_prompt_builder,
        pages_text_take=7,
    )


def parse_travel_profile(user_request: str, *, today_iso: str) -> Tuple[Dict[str, Any], List[str]]:
    """
    Парсинг профиля из user_request.

    Возвращает:
    - travel_profile (dict)
    - missing_fields (list[str]) — если пусто, profile считается готовым.
    """
    required_fields = ["destination", "datesOrMonth", "passportCountry", "purpose"]

    prompt = f"""Extract a travel profile from the user request.

Return ONLY valid JSON with these keys:
- destination: string (city or country)
- datesOrMonth: string (e.g., "2026-05-10 to 2026-05-20" or "May 2026" or "next month")
- passportCountry: string (country of passport/citizenship)
- purpose: string (e.g., tourism, business, visiting friends)
- travelStyle: string (optional; e.g., budget, luxury, active)
- budget: string (optional)
- travelers: string (optional, e.g., "2 adults", "family with kids")

If any required field is missing, set it to null.
today_iso: {today_iso}
user_request: {user_request}"""

    raw = llm.invoke([HumanMessage(content=prompt)]).content
    # Пытаемся вытащить JSON.
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        travel_profile = {
            "destination": None,
            "datesOrMonth": None,
            "passportCountry": None,
            "purpose": None,
            "travelStyle": None,
            "budget": None,
            "travelers": None,
        }
    else:
        try:
            travel_profile = json.loads(m.group(0))
        except Exception:
            travel_profile = {
                "destination": None,
                "datesOrMonth": None,
                "passportCountry": None,
                "purpose": None,
                "travelStyle": None,
                "budget": None,
                "travelers": None,
            }

    missing_fields: List[str] = []
    for f in required_fields:
        v = travel_profile.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing_fields.append(f)

    # Нормализуем ключи для остального кода (согласуем названия).
    travel_profile_norm = dict(travel_profile)
    if "datesOrMonth" in travel_profile_norm:
        travel_profile_norm["datesOrMonth"] = travel_profile_norm.get("datesOrMonth")

    # Subagents ожидают destination/passportCountry/purpose.
    return travel_profile_norm, missing_fields


def aggregate_answer(
    *,
    travel_profile: Dict[str, Any],
    official_material: Dict[str, Any],
    reviews_material: Dict[str, Any],
    practical_material: Dict[str, Any],
    today_iso: str,
) -> str:
    """Агрегация в практический финальный текст ответа."""
    # Собираем URL для секции Sources.
    urls: List[str] = []
    for mat in (official_material, reviews_material, practical_material):
        for p in mat.get("pages", []) or []:
            u = p.get("url")
            if u and u not in urls:
                urls.append(u)

    sources_block = "\n".join([f"- {u}" for u in urls[:25]])

    prompt = f"""You are a travel recommendations writer.

Goal: produce a practical travel guide for the user_request.

Inputs:
travel_profile: {json.dumps(travel_profile, ensure_ascii=False)}
today_iso: {today_iso}

Official/authoritative extracts summary:
{official_material.get('summary', '')}

Real-world reviews summary:
{reviews_material.get('summary', '')}

Practical/gear recommendations summary:
{practical_material.get('summary', '')}

Rules for the final answer:
- Write in Russian.
- Use this structure (exact headings):
  1) "Что нужно знать официально"
  2) "Что говорят люди (реальный опыт)"
  3) "Практический план перед поездкой"
  4) "Что подготовить/купить (и как не ошибиться)"
  5) "Чеклист"
  6) "Источники"
- In section 1: separate "Requirements" vs "Unconfirmed" if mentioned.
- In section 4: if something is an "Advice" vs "Requirements", label it accordingly.
- Avoid repeating long quotes; prefer concise bullets.
- If summaries contain "UNCONFIRMED", keep it as unconfirmed (do not state it as fact).
- At the end, in "Источники" include the bullet list of URLs from Sources section below.

SOURCES URLS (use as-is):
{sources_block if sources_block else '- (no sources)'}"""

    return llm.invoke([HumanMessage(content=prompt)]).content


def travel_coordinator(user_request: str, *, today_iso: str) -> Dict[str, Any]:
    """
    Координатор travel-рекомендаций.

    Возвращает dict в одном из режимов:
    - { "mode": "QUESTIONS_TO_USER", "questions": [...] }
    - { "mode": "APPROVED", "answer": "..." }
    - { "mode": "REWRITE", "candidate_answer": "...", "critique": "..." }  (как внутренний статус)
    """
    print("[TRAVEL] coordinator start")
    print(f"[TRAVEL] today_iso={today_iso}")
    print(f"[TRAVEL] user_request={truncate_text(user_request, 1000)}")

    travel_profile, missing_fields = parse_travel_profile(user_request, today_iso=today_iso)
    print(f"[TRAVEL] parsed travel_profile destination={travel_profile.get('destination')} passportCountry={travel_profile.get('passportCountry')} purpose={travel_profile.get('purpose')}")
    print(f"[TRAVEL] missing_fields={missing_fields}")
    if missing_fields:
        questions_map = {
            "destination": "Куда именно вы едете (город/страна)?",
            "datesOrMonth": "Какие даты поездки или хотя бы месяц/год?",
            "passportCountry": "Какой у вас паспорт/гражданство (страна)?",
            "purpose": "Цель поездки (туризм/бизнес/учёба/визит и т.п.)?",
        }
        questions = [questions_map[f] for f in missing_fields if f in questions_map]
        print("[TRAVEL] returning QUESTIONS_TO_USER")
        return {
            "mode": "QUESTIONS_TO_USER",
            "questions": questions,
            "missing_fields": missing_fields,
            "travel_profile": travel_profile,
        }

    def extract_focus_plan(critique: str) -> Dict[str, Any]:
        focus_prompt = f"""You are an assistant that maps fact-checker critique into targeted follow-up searches for 3 subagents.

Given:
travel_profile: {json.dumps(travel_profile, ensure_ascii=False)}
fact_check_critique:
{critique}

Return ONLY valid JSON with:
- rerun: object with boolean fields: official (1), reviews (2), practical (3)
- official_focus_claims: array of strings (may be empty)
- reviews_focus_claims: array of strings (may be empty)
- practical_focus_claims: array of strings (may be empty)

Rules:
- If critique mentions visa/entry/document rules -> official should get claims.
- If critique mentions "people experience" / timing/costs enforced differently -> reviews should get claims.
- If critique mentions gear/power/adapter/SIM/customs for items -> practical should get claims.
"""
        raw_json = llm.invoke([HumanMessage(content=focus_prompt)]).content
        m2 = re.search(r"\{.*\}", raw_json, flags=re.DOTALL)
        if not m2:
            return {
                "rerun": {"official": True, "reviews": True, "practical": True},
                "official_focus_claims": [],
                "reviews_focus_claims": [],
                "practical_focus_claims": [],
            }
        try:
            return json.loads(m2.group(0))
        except Exception:
            return {
                "rerun": {"official": True, "reviews": True, "practical": True},
                "official_focus_claims": [],
                "reviews_focus_claims": [],
                "practical_focus_claims": [],
            }

    print("[TRAVEL] running official_docs_subagent...")
    official_material = official_docs_subagent(
        today_iso=today_iso, travel_profile=travel_profile, focus_claims=None
    )
    print(f"[TRAVEL] official_docs_subagent done pages={len(official_material.get('pages', []) or [])}")

    print("[TRAVEL] running reviews_subagent...")
    reviews_material = reviews_subagent(
        today_iso=today_iso, travel_profile=travel_profile, focus_claims=None
    )
    print(f"[TRAVEL] reviews_subagent done pages={len(reviews_material.get('pages', []) or [])}")

    print("[TRAVEL] running practical_recs_subagent...")
    practical_material = practical_recs_subagent(
        today_iso=today_iso, travel_profile=travel_profile, focus_claims=None
    )
    print(f"[TRAVEL] practical_recs_subagent done pages={len(practical_material.get('pages', []) or [])}")

    print("[TRAVEL] aggregating answer...")
    candidate_answer = aggregate_answer(
        travel_profile=travel_profile,
        official_material=official_material,
        reviews_material=reviews_material,
        practical_material=practical_material,
        today_iso=today_iso,
    )
    print(f"[TRAVEL] candidate_answer length={len(candidate_answer)}")

    rewrite_count = 0
    last_candidate = candidate_answer
    factcheck_history: List[Dict[str, Any]] = []

    while True:
        print(f"[TRAVEL] starting fact-check gate (attempt {rewrite_count+1}/3)")
        verdict = run_fact_check_gate(candidate_answer, today_iso=today_iso)
        print(f"[TRAVEL] fact-check status={verdict.get('status')}")
        critique = verdict.get("critique", "") or ""
        factcheck_history.append(
            {
                "attempt": rewrite_count + 1,
                "status": verdict.get("status"),
                "critique": critique,
                "candidate_answer_length": len(candidate_answer or ""),
            }
        )
        if verdict["status"] == "APPROVED":
            print("[TRAVEL] fact-check APPROVED")
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            out_file = f"sources_travel_{ts}.json"
            _save_sources_json(
                {
                    "generated_at_utc": datetime.utcnow().isoformat() + "Z",
                    "user_request": user_request,
                    "today_iso": today_iso,
                    "travel_profile": travel_profile,
                    "official_material": official_material,
                    "reviews_material": reviews_material,
                    "practical_material": practical_material,
                    "candidate_answer": candidate_answer,
                    "factcheck_history": factcheck_history,
                    "rewrite_count": rewrite_count,
                },
                filename=out_file,
            )
            return {"mode": "APPROVED", "answer": candidate_answer}

        # verdict == REWRITE
        if rewrite_count >= 2:
            print("[TRAVEL] rewrite limit reached")
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            out_file = f"sources_travel_{ts}.json"
            _save_sources_json(
                {
                    "generated_at_utc": datetime.utcnow().isoformat() + "Z",
                    "user_request": user_request,
                    "today_iso": today_iso,
                    "travel_profile": travel_profile,
                    "official_material": official_material,
                    "reviews_material": reviews_material,
                    "practical_material": practical_material,
                    "candidate_answer": candidate_answer,
                    "factcheck_history": factcheck_history,
                    "rewrite_count": rewrite_count,
                },
                filename=out_file,
            )
            return {
                "mode": "APPROVED",
                "answer": candidate_answer,
                "note": "Fact-checker requested rewrite, but rewrite limit reached.",
                "last_critique": critique,
            }

        print(f"[TRAVEL] fact-check REWRITE critique (truncated): {truncate_text(critique, 2500)}")
        focus_plan = extract_focus_plan(critique)
        rerun = focus_plan.get("rerun", {})
        print(f"[TRAVEL] rerun plan: {rerun}")

        if rerun.get("official", True):
            print("[TRAVEL] rerunning official_docs_subagent due to critique...")
            official_material = official_docs_subagent(
                today_iso=today_iso,
                travel_profile=travel_profile,
                focus_claims=focus_plan.get("official_focus_claims") or None,
                existing_material=official_material,
            )
        if rerun.get("reviews", True):
            print("[TRAVEL] rerunning reviews_subagent due to critique...")
            reviews_material = reviews_subagent(
                today_iso=today_iso,
                travel_profile=travel_profile,
                focus_claims=focus_plan.get("reviews_focus_claims") or None,
                existing_material=reviews_material,
            )
        if rerun.get("practical", True):
            print("[TRAVEL] rerunning practical_recs_subagent due to critique...")
            practical_material = practical_recs_subagent(
                today_iso=today_iso,
                travel_profile=travel_profile,
                focus_claims=focus_plan.get("practical_focus_claims") or None,
                existing_material=practical_material,
            )

        # Переписываем с учетом критики (аккуратно, чтобы структура осталась практической).
        rewrite_prompt = f"""Rewrite the travel guide to fix the fact-checker critique.

Requirements:
- Keep the exact same section headings as in the original aggregated answer.
- Correct or remove any claims mentioned in fact-checker critique.
- Do not add new strong claims without sources from subagent summaries.

travel_profile: {json.dumps(travel_profile, ensure_ascii=False)}
today_iso: {today_iso}
fact_check_critique:
{critique}

Original candidate_answer:
{candidate_answer}

official_material_summary:
{official_material.get('summary', '')}
reviews_material_summary:
{reviews_material.get('summary', '')}
practical_material_summary:
{practical_material.get('summary', '')}
"""
        print("[TRAVEL] rewriting candidate_answer...")
        candidate_answer = llm.invoke([HumanMessage(content=rewrite_prompt)]).content
        last_candidate = candidate_answer
        rewrite_count += 1


def _log_api_diagnostic():
    """При ошибке — тестовый запрос и вывод сырого ответа API."""
    print("\n[API diagnostic] Request to OpenRouter:")
    r = requests.post(
        CHAT_URL,
        json={"model": model, "messages": [{"role": "user", "content": "Hello"}]},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com",
        },
        timeout=30,
    )
    print(f"  HTTP {r.status_code}")
    try:
        print("  Body:", json.dumps(r.json(), ensure_ascii=False, indent=2)[:1500])
    except Exception:
        print("  Body (raw):", r.text[:1000])


def _messages_to_api(messages):
    out = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content or ""})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content or ""})
        elif isinstance(m, AIMessage):
            out.append({"role": "assistant", "content": m.content or ""})
        else:
            out.append({"role": "user", "content": str(getattr(m, "content", m)) or ""})
    return out


class OpenRouterChat:
    """Чат-модель для OpenRouter API (стандартный OpenAI-формат: choices в корне)."""

    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        temperature: float = 0,
        timeout: int = 60,
    ):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def invoke(self, messages):
        payload = {
            "model": self.model,
            "messages": _messages_to_api(messages),
            "temperature": self.temperature,
        }
        r = requests.post(
            self.url,
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com",
            },
            timeout=self.timeout,
        )
        if not r.ok:
            raise SystemExit(f"API HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        # OpenRouter: choices в корне (как у OpenAI)
        choices = data.get("choices")
        if not choices:
            _log_api_diagnostic()
            raise SystemExit(
                "API returned response without choices. See raw response above. Check key and quota."
            )
        msg = choices[0].get("message") or {}
        return AIMessage(content=msg.get("content") or "")


llm = OpenRouterChat(CHAT_URL, token, model, temperature=0)


# --- Агент: инструменты по маркеру SEARCH: в ответе модели ---
SEARCH_MARKER = "SEARCH:"
SYSTEM_PROMPT = f"""Today's date: {date.today().isoformat()}!!!!!!THIS IS IMPORTANT!!!!!!\n\n
You are a Fact-Checker AI Agent. Strict order:
1) You receive a claim and search results. First, study the search results.
2) Identify all factual claims in the statement. You must verify EVERY factual claim for truthfulness.
3) If the data is sufficient for a conclusion — give the verdict immediately (short summary and sources). Do not search unnecessarily.
4) If something is missing — write exactly one line: {SEARCH_MARKER} refined query (different from any you already used). Wait for the next search result, then decide: verdict or another query.
Rely on the search results you already have; use general knowledge only to refine queries, not to draw conclusions. Do not duplicate queries.
If the claim contains several factual assertions, verify ALL of them for truthfulness.

Strict output format requirements:
- If you can verify that the statement is correct (i.e., no important factual errors and everything is supported), output:
APPROVED
<short verdict in Russian/English>
<optional "Sources:" list>
- If you find factual assertions that are NOT supported, contradicted, or materially incorrect given the evidence, output exactly:
REWRITE:
<bullet list of problems: each problem should reference the related assertion and explain what is wrong or unconfirmed>
<optional "Sources:" list if you have them>
- If you need more evidence: output exactly one line starting with {SEARCH_MARKER} and nothing else."""


FACTCHECK_TODAY_ISO: Optional[str] = None


def _extract_search_query(text: str) -> Optional[str]:
    m = re.search(
        rf"{re.escape(SEARCH_MARKER)}\s*(.+?)(?:\n|$)", text.strip(), re.DOTALL
    )
    return m.group(1).strip() if m else None


def agent_node(state: MessagesState):
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    resp_text = getattr(response, "content", "") or ""
    print(f"[FACTCHECK] agent_node response head: {truncate_text(resp_text, 2500)}")
    return {"messages": [response]}


def tools_node(state: MessagesState):
    """Парсим SEARCH: из последнего ответа модели, вызываем search_web, добавляем результат в историю."""
    last = state["messages"][-1]
    content = getattr(last, "content", "") or ""
    query = _extract_search_query(content)
    if not query:
        return {"messages": []}
    print(f"[FACTCHECK] tools_node extracted SEARCH query: {query}")
    result = search_web(query, today_iso=FACTCHECK_TODAY_ISO, max_results=3)
    print("   [✓ Search result received]")
    return {
        "messages": [
            HumanMessage(
                content=f"[Search result for query «{query}»]:\n{result}"
            )
        ]
    }


def route_after_agent(state: MessagesState) -> str:
    last = state["messages"][-1]
    content = getattr(last, "content", "") or ""
    return "tools" if SEARCH_MARKER in content else "__end__"


def research_node(state: MessagesState):
    """Обязательный первый поиск по утверждению — агент всегда получает результат поиска до вердикта."""
    messages = list(state["messages"])
    if not messages:
        return {"messages": []}
    claim_text = getattr(messages[-1], "content", "") or str(messages[-1])
    if not claim_text.strip():
        return {"messages": []}

    # Чтобы не пытаться искать по целому "ответу" (он большой), генерируем короткий стартовый поисковый запрос.
    init_prompt = """Generate ONE short web search query to fact-check the most important travel-related factual claims in the given text.
Return ONLY the query text (no prefixes, no quotes). Include the destination/country keywords if present.
If today ISO is provided in the system message, include its year in the query."""
    init_messages = [
        SystemMessage(content=init_prompt),
        HumanMessage(
            content=f"today_iso: {FACTCHECK_TODAY_ISO}\nTEXT_TO_CHECK:\n{claim_text[:6000]}"
        ),
    ]
    query = llm.invoke(init_messages).content.strip().splitlines()[0]
    if not query:
        query = claim_text[:120].strip()
    print(f"[FACTCHECK] research_node initial query: {query}")
    result = search_web(query, today_iso=FACTCHECK_TODAY_ISO, max_results=3)
    print("   [✓ Search result received]")
    return {
        "messages": [
            HumanMessage(
                content=f"[Search result for query «{query}»]:\n{result}"
            )
        ]
    }


def run_fact_check_gate(candidate_answer: str, *, today_iso: str) -> Dict[str, str]:
    """
    Прогоняет существующий fact-checker граф и возвращает машиночитаемый verdict:
    - {"status": "APPROVED", "critique": "..."}
    - {"status": "REWRITE", "critique": "..."}
    """
    global FACTCHECK_TODAY_ISO
    FACTCHECK_TODAY_ISO = today_iso
    print("[FACTCHECK] gate start")
    final_text = ""
    for event in researcher_graph.stream({"messages": [HumanMessage(content=candidate_answer)]}):
        for node_name, node_state in event.items():
            if node_name == "agent":
                last = node_state["messages"][-1]
                final_text = getattr(last, "content", "") or ""

    if not final_text.strip():
        return {"status": "REWRITE", "critique": "Fact-checker returned empty verdict."}

    if final_text.strip().startswith("APPROVED"):
        return {"status": "APPROVED", "critique": final_text.strip()}
    if final_text.strip().startswith("REWRITE:") or final_text.strip().startswith("REWRITE"):
        critique = final_text.strip()
        print("[FACTCHECK] gate REWRITE")
        return {"status": "REWRITE", "critique": critique}

    # Фоллбек на случай формата.
    if SEARCH_MARKER in final_text:
        print("[FACTCHECK] gate REWRITE (fallback SEARCH marker)")
        return {"status": "REWRITE", "critique": "Fact-checker did not finish properly (still requested SEARCH)."}
    print("[FACTCHECK] gate REWRITE (fallback)")
    return {"status": "REWRITE", "critique": final_text.strip()}


# --- LangGraph ---
builder = StateGraph(MessagesState)
builder.add_node("research", research_node)
builder.add_node("agent", agent_node)
builder.add_node("tools", tools_node)
builder.add_edge(START, "research")
builder.add_edge("research", "agent")
builder.add_conditional_edges("agent", route_after_agent)
builder.add_edge("tools", "agent")
researcher_graph = builder.compile()


if __name__ == "__main__":
    import sys

    user_request = (
        "Хочу в ноябре 2026 года в Австралию"
    )
    args = sys.argv[1:]
    if "--selftest-html" in args:
        sample_html = """
        <html><head><style>.x{color:red}</style><script>var a=1;</script></head>
        <body><h1>Hello</h1><p>World <b>from</b> HTML.</p><nav>Menu</nav></body></html>
        """
        print("HTML selftest output:")
        print(extract_visible_text(sample_html))
        raise SystemExit(0)
    if args:
        user_request = " ".join(args)

    today_iso = date.today().isoformat()
    print(f"today_iso: {today_iso}")
    print("-" * 60)
    print("user_request:")
    print(user_request)
    print("-" * 60)

    current_request = user_request
    max_question_rounds = 10
    question_round = 0

    while True:
        result = travel_coordinator(current_request, today_iso=today_iso)
        mode = result.get("mode")

        if mode == "QUESTIONS_TO_USER":
            question_round += 1
            if question_round > max_question_rounds:
                print("\nToo many question rounds; stopping.")
                break

            questions = result.get("questions", [])
            missing_fields = result.get("missing_fields") or []

            # For non-interactive use (piped input), keep old CLI behavior:
            # show questions and exit without waiting.
            if not sys.stdin.isatty():
                print("\nQUESTIONS_TO_USER:")
                for i, q in enumerate(questions, start=1):
                    print(f"{i}. {q}")
                break

            print("\nQUESTIONS_TO_USER:")
            for i, q in enumerate(questions, start=1):
                field = missing_fields[i - 1] if i - 1 < len(missing_fields) else None
                answer = input(f"{i}. {q}\n> ").strip()
                if not answer:
                    # If user enters empty value, still append it to keep extraction consistent.
                    answer = ""
                if field:
                    current_request = f"{current_request}\n{field}: {answer}"
                else:
                    # Fallback: at least include the text response.
                    current_request = f"{current_request}\nanswer{i}: {answer}"
            continue

        if mode == "APPROVED":
            print("\nAPPROVED:")
            print(result.get("answer", ""))
            if result.get("note"):
                print("\nNOTE:")
                print(result["note"])
            if result.get("last_critique"):
                print("\nLAST_CRITIQUE:")
                print(result["last_critique"])
            break

        print("\nUnexpected result mode:", mode)
        break
