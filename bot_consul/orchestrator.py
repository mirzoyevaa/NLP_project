"""
Оркестратор: guardrails → профиль → RAG (Qdrant) → при необходимости веб-агент (travel_web_agent)
→ короткий DDG (web_fallback) → генерация → опц. fact-check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from bot_consul.config import orchestrator_settings
from bot_consul.user_id_log import hash_session_id_for_log
from bot_consul.guardrails import check_guardrails
from bot_consul.llm_client import OpenRouterChat
from bot_consul.profile import (
    QUESTIONS_MAP,
    normalize_country_from_destination,
    normalize_visa_type_for_store,
    parse_visa_profile,
)
from bot_consul.prompts import (
    SYSTEM_CONSUL_FOLLOWUP,
    SYSTEM_CONSUL_INITIAL,
    build_followup_user_prompt,
    build_user_prompt,
)
from bot_consul.rag_service import RAGService, assess_rag_sufficiency
from bot_consul.session import SessionState, VisaProfile, get_session, merge_visa_profile
from bot_consul.travel_bridge import (
    format_travel_web_for_prompt,
    run_travel_web_subagents,
    visa_profile_to_travel_profile,
)
from bot_consul.web_fallback import fetch_web_snippets_pair
from bot_consul.web_source_catalog import persist_filtered_sources

logger = logging.getLogger(__name__)


class TurnMode(str, Enum):
    REFUSED = "refused"
    NEED_CLARIFICATION = "need_clarification"
    ANSWER = "answer"
    ERROR = "error"


@dataclass
class OrchestratorResult:
    mode: TurnMode
    message: str
    meta: Dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        llm: Optional[OpenRouterChat] = None,
        rag: Optional[RAGService] = None,
    ):
        self._llm = llm
        self._rag = rag or RAGService()

    @property
    def llm(self) -> OpenRouterChat:
        if self._llm is None:
            self._llm = OpenRouterChat()
        return self._llm

    def run_turn(self, session_id: str, user_message: str) -> OrchestratorResult:
        session = get_session(session_id)
        text = (user_message or "").strip()
        logger.info(
            "run_turn start user_hash=%s len=%s",
            hash_session_id_for_log(session_id),
            len(text),
        )

        gr = check_guardrails(text)
        if not gr.allowed:
            session.add_user(text)
            session.add_assistant(gr.user_message or "")
            return OrchestratorResult(
                mode=TurnMode.REFUSED,
                message=gr.user_message or "",
                meta={"reason": gr.reason},
            )

        session.add_user(text)

        hist = orchestrator_settings.HISTORY_MAX_TURNS
        dialog_ctx = session.recent_context(hist)

        try:
            parsed, _missing = parse_visa_profile(
                text,
                dialog_context=dialog_ctx,
                llm=self.llm,
            )
        except Exception as e:
            logger.exception("parse_visa_profile failed")
            session.add_assistant("Не удалось разобрать запрос. Попробуйте переформулировать.")
            return OrchestratorResult(
                mode=TurnMode.ERROR,
                message="Не удалось разобрать запрос. Попробуйте переформулировать.",
                meta={"error": str(e)},
            )

        merge_visa_profile(session.profile, parsed)
        if session.profile.destination:
            session.profile.country = normalize_country_from_destination(
                session.profile.destination
            )

        missing = session.profile.missing_required()
        if missing:
            questions = [QUESTIONS_MAP[f] for f in missing if f in QUESTIONS_MAP]
            session.pending_clarification_fields = missing
            if len(questions) == 1:
                ask = questions[0]
            elif questions:
                ask = "\n".join(f"• {q}" for q in questions)
            else:
                ask = "Уточните страну, гражданство и цель поездки."
            session.add_assistant(ask)
            return OrchestratorResult(
                mode=TurnMode.NEED_CLARIFICATION,
                message=ask,
                meta={"missing_fields": missing},
            )

        session.pending_clarification_fields = []

        # Новая поездка / другое направление → снова первичный шаблон и кнопка «полезно» (как после /reset)
        dest_key = session.profile.country or normalize_country_from_destination(
            session.profile.destination
        )
        if not dest_key and session.profile.destination:
            dest_key = (session.profile.destination or "").strip().lower()[:120] or None
        if (
            session.initial_brief_done
            and dest_key
            and session.brief_country_key
            and dest_key != session.brief_country_key
        ):
            session.initial_brief_done = False
            session.feedback_buttons_shown = False
            logger.info(
                "Смена направления (%s → %s): снова первичный ответ и шаблон",
                session.brief_country_key,
                dest_key,
            )

        followup_turn = session.initial_brief_done

        q = self._build_rag_query(text, session.profile, session)
        visa_filter = normalize_visa_type_for_store(session.profile.visa_type)

        try:
            bundle = self._rag.retrieve(
                q,
                country=session.profile.country,
                visa_type=visa_filter,
            )
        except Exception as e:
            logger.exception("RAG retrieve failed")
            session.add_assistant(
                "Не удалось обратиться к базе знаний. Попробуйте позже или проверьте Qdrant."
            )
            return OrchestratorResult(
                mode=TurnMode.ERROR,
                message="Не удалось обратиться к базе знаний. Попробуйте позже.",
                meta={"error": str(e)},
            )

        rag_ok, rag_reason = assess_rag_sufficiency(
            bundle,
            country_code=session.profile.country,
        )
        logger.info("RAG sufficiency: ok=%s (%s)", rag_ok, rag_reason)

        # Единая контрольная дата «сегодня» для промпта, JSON-артефактов и fact-check.
        today_iso = date.today().isoformat()

        # Три сабагента travel_web: на первичном ответе и на уточнениях (если TRAVEL_WEB_MODE != off).
        # Отключение только уточнений: FOLLOWUP_SKIP_TRAVEL_WEB=true в .env.
        tw_mode = (orchestrator_settings.TRAVEL_WEB_MODE or "off").lower()
        run_travel = tw_mode != "off" and not (
            followup_turn and orchestrator_settings.FOLLOWUP_SKIP_TRAVEL_WEB
        )

        travel_web_block = ""
        travel_meta: Optional[Dict[str, Any]] = None
        travel_web_error: Optional[str] = None
        if run_travel:
            try:
                tp = visa_profile_to_travel_profile(session.profile)
                travel_meta = run_travel_web_subagents(tp, today_iso=today_iso)
                travel_web_block = format_travel_web_for_prompt(travel_meta)
            except Exception as e:
                logger.exception("travel_web_agent failed: %s", e)
                travel_web_error = str(e)[:4000]

        # Короткий DDG при слабом RAG (force), иначе — только если ENABLE_WEB_FALLBACK
        ddg_web_block, ddg_raw = fetch_web_snippets_pair(
            self._build_web_query(session.profile, text, session),
            today_iso=today_iso,
            force=not rag_ok,
        )

        # JSON в data/raw/: раньше писался только travel_web_*.json при успешном travel_meta —
        # из‑за этого «в хранилище не появляются новые файлы», если веб-агент упал или выключен.
        # Теперь при SAVE_SEARCH_ARTIFACTS_JSON всегда сохраняем turn_*.json (полный снимок хода).
        if orchestrator_settings.SAVE_SEARCH_ARTIFACTS_JSON:
            try:
                from search_artifacts import (
                    data_raw_dir,
                    ddg_results_for_artifact,
                    save_json_artifact,
                    utc_now_iso,
                )

                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                turn_payload: Dict[str, Any] = {
                    "kind": "orchestrator_turn",
                    "generated_at_utc": utc_now_iso(),
                    "today_iso": today_iso,
                    "user_query": text,
                    "phase": "followup" if followup_turn else "initial",
                    "travel_web_mode": tw_mode,
                    "travel_web_ran": run_travel,
                    "travel_web_error": travel_web_error,
                    "travel_profile": visa_profile_to_travel_profile(session.profile),
                    "travel_meta": travel_meta,
                    "rag_sufficient": rag_ok,
                    "rag_reason": rag_reason,
                    "rag_chunk_count": len(bundle.chunks),
                    "ddg_raw_count": len(ddg_raw or []),
                    "ddg_results_preview": ddg_results_for_artifact(ddg_raw or []),
                }
                p_turn = save_json_artifact(turn_payload, filename=f"turn_{ts}.json")
                if p_turn:
                    logger.info(
                        "Артефакт JSON хода: %s (каталог: %s)",
                        p_turn,
                        data_raw_dir(),
                    )

                if travel_meta:
                    save_json_artifact(
                        {
                            "kind": "travel_web_subagents",
                            "generated_at_utc": utc_now_iso(),
                            "user_query": text,
                            "today_iso": today_iso,
                            "travel_profile": visa_profile_to_travel_profile(session.profile),
                            "official": travel_meta.get("official"),
                            "reviews": travel_meta.get("reviews"),
                            "practical": travel_meta.get("practical"),
                        },
                        filename=f"travel_web_{ts}.json",
                    )
            except Exception as e:
                logger.warning("Не удалось сохранить JSON-артефакты: %s", e)
        if not rag_ok and not ddg_web_block:
            logger.warning(
                "Слабый RAG, но DDG пусто — ответ пойдёт без сниппетов веб-поиска "
                "(проверьте ddgs, сеть или ENABLE_WEB_FALLBACK для доп. запросов)."
            )

        try:
            persist_filtered_sources(
                session_id=session_id,
                user_query=text,
                profile=session.profile,
                travel_meta=travel_meta,
                ddg_results=ddg_raw or [],
                today_iso=today_iso,
            )
        except Exception as e:
            logger.warning("persist_filtered_sources: %s", e)

        profile_block = self._format_profile(session.profile)
        rag_block = bundle.texts_for_prompt()

        prior_dialog = session.prior_dialog_for_prompt(hist)

        if followup_turn:
            user_prompt = build_followup_user_prompt(
                user_query=text,
                profile_block=profile_block,
                rag_block=rag_block,
                ddg_web_block=ddg_web_block,
                prior_dialog=prior_dialog,
                rag_sufficient=rag_ok,
                rag_reason=rag_reason,
                today_iso=today_iso,
            )
            system_content = SYSTEM_CONSUL_FOLLOWUP
        else:
            user_prompt = build_user_prompt(
                user_query=text,
                profile_block=profile_block,
                rag_block=rag_block,
                ddg_web_block=ddg_web_block,
                travel_web_block=travel_web_block,
                rag_sufficient=rag_ok,
                rag_reason=rag_reason,
                prior_dialog=prior_dialog,
                today_iso=today_iso,
            )
            system_content = SYSTEM_CONSUL_INITIAL

        if not followup_turn and not rag_ok and not travel_web_block and not ddg_web_block:
            user_prompt += (
                "\n\nПримечание: в базе знаний мало релевантных фрагментов ("
                f"{rag_reason}). Предупреди пользователя и предложи официальный сайт консульства."
            )

        try:
            ans = self.llm.invoke(
                [
                    SystemMessage(content=system_content),
                    HumanMessage(content=user_prompt),
                ]
            )
            answer = (ans.content or "").strip() or "Пустой ответ модели."
        except Exception as e:
            logger.exception("LLM generation failed")
            session.add_assistant("Ошибка генерации ответа. Попробуйте позже.")
            return OrchestratorResult(
                mode=TurnMode.ERROR,
                message="Ошибка генерации ответа. Попробуйте позже.",
                meta={"error": str(e)},
            )

        if orchestrator_settings.ENABLE_TRAVEL_FACT_CHECK:
            try:
                from fact_check_openrouter import run_fact_check_gate

                max_fc = max(1, orchestrator_settings.FACT_CHECK_MAX_REVERIFY_ATTEMPTS)
                prior_reverify: Optional[str] = None
                for fc_i in range(max_fc):
                    fm = run_fact_check_gate(
                        answer,
                        today_iso=today_iso,
                        reverify_note=prior_reverify,
                    )
                    st = (fm.get("status") or "").upper()
                    if st == "APPROVED":
                        logger.info("fact-check: APPROVED на попытке %s/%s", fc_i + 1, max_fc)
                        break
                    if st == "REVERIFY" and fc_i < max_fc - 1:
                        prior_reverify = fm.get("critique", "") or ""
                        logger.info(
                            "fact-check: REVERIFY — повтор полного прогона %s/%s",
                            fc_i + 2,
                            max_fc,
                        )
                        continue
                    logger.info(
                        "fact-check: финал со статусом %s (попытка %s/%s)",
                        st,
                        fc_i + 1,
                        max_fc,
                    )
                    break
                # Результат фактчека не показываем пользователю (ни дисклеймеров, ни терминов про проверку).
            except Exception as e:
                logger.exception("fact_check: %s", e)

        session.add_assistant(answer)
        # Первый содержательный ответ по первичному брифу: кнопки один раз на первый такой ответ.
        show_feedback = (
            not followup_turn
            and not session.feedback_buttons_shown
        )
        if not followup_turn:
            session.initial_brief_done = True
            session.brief_country_key = (
                session.profile.country
                or normalize_country_from_destination(session.profile.destination)
                or (session.profile.destination or "").strip().lower()[:120]
                or None
            )
        if show_feedback:
            session.feedback_buttons_shown = True

        return OrchestratorResult(
            mode=TurnMode.ANSWER,
            message=answer,
            meta={
                "phase": "followup" if followup_turn else "initial",
                # Первый полный ответ по сценарию «первичный бриф» — клавиатура в Telegram на первом chunk
                "show_feedback_buttons": show_feedback,
                "rag_sufficient": rag_ok,
                "rag_reason": rag_reason,
                "chunks": len(bundle.chunks),
                "ddg_web_used": bool(ddg_web_block),
                "travel_web_used": bool(travel_web_block),
                "travel_web_mode": tw_mode,
            },
        )

    @staticmethod
    def _build_rag_query(user_text: str, profile: VisaProfile, session: SessionState) -> str:
        parts = [user_text]
        user_msgs = [m.content for m in session.history if m.role == "user"]
        if len(user_msgs) >= 2:
            prev = (user_msgs[-2] or "").strip()
            if prev and prev != user_text.strip() and len(prev) < 1200:
                parts.append(f"Предыдущий вопрос пользователя: {prev}")
        if profile.destination:
            parts.append(f"Страна/направление: {profile.destination}")
        if profile.purpose:
            parts.append(f"Цель: {profile.purpose}")
        if profile.passport_country:
            parts.append(f"Паспорт: {profile.passport_country}")
        return "\n".join(parts)

    @staticmethod
    def _build_web_query(profile: VisaProfile, user_text: str, session: SessionState) -> str:
        dest = profile.destination or ""
        pc = profile.passport_country or ""
        base = f"{user_text} {dest} visa entry requirements {pc}".strip()
        user_msgs = [m.content for m in session.history if m.role == "user"]
        if len(user_msgs) >= 2:
            p = (user_msgs[-2] or "").strip()
            if p and len(p) < 400:
                base = f"{base} {p}"
        return base.strip()

    @staticmethod
    def _format_profile(p: VisaProfile) -> str:
        lines = [
            f"destination: {p.destination or '—'}",
            f"passport_country: {p.passport_country or '—'}",
            f"purpose: {p.purpose or '—'}",
            f"visa_type (raw): {p.visa_type or '—'}",
            f"dates: {p.dates_or_month or '—'}",
            f"country_code (normalized): {p.country or '—'}",
        ]
        return "\n".join(lines)


def run_turn(session_id: str, user_message: str) -> OrchestratorResult:
    return Orchestrator().run_turn(session_id, user_message)
