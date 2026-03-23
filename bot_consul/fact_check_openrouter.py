"""
Фактчек (LangGraph + маркер SEARCH: + DDG) — каноническая реализация пути **agent_openrouter**.

Исторически код жил в ``travel_web_agent.py``; вынесен сюда, чтобы:
- ``agent_openrouter (1).py`` и оркестратор явно опирались на один модуль;
- логику графа не смешивали с travel-сабагентами и не «дорабатывали» отдельно.

Поиск: ``search_web`` импортируется лениво из ``travel_web_agent`` (избегаем циклического импорта при загрузке).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import START, MessagesState, StateGraph

from bot_consul.llm_client import get_llm


def _truncate_for_log(text: str, max_chars: int = 2500) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 10] + "...(truncated)..."


def _search_web(*args, **kwargs):
    from travel_web_agent import search_web

    return search_web(*args, **kwargs)


# --- Агент: инструменты по маркеру SEARCH: в ответе модели ---
SEARCH_MARKER = "SEARCH:"


def _build_factcheck_system_prompt(today_iso: str) -> str:
    """Контрольная дата передаётся из оркестратора (и в human-сообщениях research_node как today_iso)."""
    iso = (today_iso or "").strip() or date.today().isoformat()
    return f"""Текущая контрольная дата (ISO «сегодня» от сервиса): {iso}. Используй её для актуальности годов в запросах и вердиктах; не опирайся на устаревший год из общих знаний, если контекст требует «сейчас».

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
- If sources clearly CONFLICT, the situation is genuinely ambiguous, or you need a completely fresh baseline search pass (not just one more SEARCH line in this same turn), output exactly:
REVERIFY:
<brief explanation in Russian: why the case is controversial and what must be re-checked in a new full pass>
- If you need more evidence within this same pass: output exactly one line starting with {SEARCH_MARKER} and nothing else."""


# Обратная совместимость: без динамики (предпочтительно вызывать _build_factcheck_system_prompt).
SYSTEM_PROMPT = _build_factcheck_system_prompt(date.today().isoformat())


FACTCHECK_TODAY_ISO: Optional[str] = None


def _extract_search_query(text: str) -> Optional[str]:
    m = re.search(
        rf"{re.escape(SEARCH_MARKER)}\s*(.+?)(?:\n|$)", text.strip(), re.DOTALL
    )
    return m.group(1).strip() if m else None


def agent_node(state: MessagesState):
    iso = FACTCHECK_TODAY_ISO or date.today().isoformat()
    messages = [
        SystemMessage(content=_build_factcheck_system_prompt(iso))
    ] + state["messages"]
    response = get_llm().invoke(messages)
    resp_text = getattr(response, "content", "") or ""
    print(f"[FACTCHECK] agent_node response head: {_truncate_for_log(resp_text, 2500)}")
    return {"messages": [response]}


def tools_node(state: MessagesState):
    """Парсим SEARCH: из последнего ответа модели, вызываем search_web, добавляем результат в историю."""
    last = state["messages"][-1]
    content = getattr(last, "content", "") or ""
    query = _extract_search_query(content)
    if not query:
        return {"messages": []}
    print(f"[FACTCHECK] tools_node extracted SEARCH query: {query}")
    result = _search_web(query, today_iso=FACTCHECK_TODAY_ISO, max_results=3)
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

    init_prompt = """Generate ONE short web search query to fact-check the most important travel-related factual claims in the given text.
Return ONLY the query text (no prefixes, no quotes). Include the destination/country keywords if present.
If today ISO is provided in the system message, include its year in the query."""
    init_messages = [
        SystemMessage(content=init_prompt),
        HumanMessage(
            content=f"today_iso: {FACTCHECK_TODAY_ISO}\nTEXT_TO_CHECK:\n{claim_text[:6000]}"
        ),
    ]
    query = get_llm().invoke(init_messages).content.strip().splitlines()[0]
    if not query:
        query = claim_text[:120].strip()
    print(f"[FACTCHECK] research_node initial query: {query}")
    result = _search_web(query, today_iso=FACTCHECK_TODAY_ISO, max_results=3)
    print("   [✓ Search result received]")
    return {
        "messages": [
            HumanMessage(
                content=f"[Search result for query «{query}»]:\n{result}"
            )
        ]
    }


def run_fact_check_gate(
    candidate_answer: str,
    *,
    today_iso: str,
    reverify_note: Optional[str] = None,
) -> Dict[str, str]:
    """
    Прогоняет fact-checker граф и возвращает verdict:
    - {"status": "APPROVED", "critique": "..."}
    - {"status": "REWRITE", "critique": "..."}
    - {"status": "REVERIFY", "critique": "..."} — оркестратор может запустить граф заново (до N раз)
    """
    global FACTCHECK_TODAY_ISO
    FACTCHECK_TODAY_ISO = today_iso
    payload = candidate_answer.strip()
    if reverify_note and reverify_note.strip():
        payload = (
            f"{payload}\n\n---\n"
            f"Перепроверка: предыдущий прогон пометил ситуацию как спорную. "
            f"Сделай новый baseline-поиск и вердикт. Контекст:\n{reverify_note.strip()}"
        )
    print("[FACTCHECK] gate start" + (" (reverify)" if reverify_note else ""))
    final_text = ""
    for event in researcher_graph.stream({"messages": [HumanMessage(content=payload)]}):
        for node_name, node_state in event.items():
            if node_name == "agent":
                last = node_state["messages"][-1]
                final_text = getattr(last, "content", "") or ""

    ft = final_text.strip()
    if not ft:
        return {"status": "REWRITE", "critique": "Fact-checker returned empty verdict."}

    if ft.startswith("APPROVED"):
        return {"status": "APPROVED", "critique": ft}
    if ft.startswith("REVERIFY:") or ft.startswith("REVERIFY\n") or ft == "REVERIFY":
        print("[FACTCHECK] gate REVERIFY (спорно — возможен повтор полного прогона)")
        return {"status": "REVERIFY", "critique": ft}
    if ft.startswith("REWRITE:") or ft.startswith("REWRITE"):
        print("[FACTCHECK] gate REWRITE")
        return {"status": "REWRITE", "critique": ft}

    if SEARCH_MARKER in final_text:
        print("[FACTCHECK] gate REWRITE (fallback SEARCH marker)")
        return {"status": "REWRITE", "critique": "Fact-checker did not finish properly (still requested SEARCH)."}
    print("[FACTCHECK] gate REWRITE (fallback)")
    return {"status": "REWRITE", "critique": ft}


# --- LangGraph ---
_builder = StateGraph(MessagesState)
_builder.add_node("research", research_node)
_builder.add_node("agent", agent_node)
_builder.add_node("tools", tools_node)
_builder.add_edge(START, "research")
_builder.add_edge("research", "agent")
_builder.add_conditional_edges("agent", route_after_agent)
_builder.add_edge("tools", "agent")
researcher_graph = _builder.compile()
