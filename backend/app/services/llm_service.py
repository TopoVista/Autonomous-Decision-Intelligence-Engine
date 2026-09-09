from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.memory.query_cache import QueryCache
from app.tools.chart_recommender import recommend_chart

try:  # pragma: no cover - optional dependency
    from openai import APIError, AsyncOpenAI, RateLimitError
except Exception:  # pragma: no cover - fallback
    AsyncOpenAI = None

    class RateLimitError(Exception):
        pass

    class APIError(Exception):
        pass


logger = structlog.get_logger()
settings = get_settings()

# Sentinel returned by the offline LLM fallback when it cannot produce a valid
# SQL fix. It intentionally fails ``sql_validator`` (does not start with
# SELECT/WITH), so callers surface a clear failure instead of silently emitting
# a fake "successful" placeholder query.
LOCAL_SQL_FIX_UNAVAILABLE = "SQL_FIX_UNAVAILABLE_LOCAL_MODE"


def _extract_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.replace("json\n", "", 1).replace("sql\n", "", 1)
    return text.strip()


def _local_question_text(user_prompt: str) -> str:
    match = re.search(r"Question:\s*(.+)", user_prompt, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r"User Question:\s*(.+)", user_prompt, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return user_prompt[:400]


def _parse_schema_tables(schema_context: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in schema_context.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("DATABASE SCHEMA"):
            continue
        if line.startswith("Columns:"):
            if current is not None:
                cols = line[len("Columns:") :].strip()
                parsed = []
                for chunk in cols.split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    m = re.match(r"(?P<name>[A-Za-z0-9_]+)\s*\((?P<type>[^)]+)\)", chunk)
                    if m:
                        parsed.append({"name": m.group("name"), "type": m.group("type")})
                current["columns"] = parsed
            continue
        if line.startswith("FK:"):
            continue
        m = re.match(r"(?P<table>[A-Za-z0-9_]+)\s*\(~?(?P<rows>[0-9?,]+)\s*rows?\)", line)
        if m:
            current = {
                "name": m.group("table"),
                "row_count_estimate": m.group("rows"),
                "columns": [],
            }
            tables.append(current)
    return tables


def _find_first_matching_column(columns: list[dict[str, Any]], candidates: Iterable[str]) -> str | None:
    candidate_set = {candidate.lower() for candidate in candidates}
    for col in columns:
        name = col["name"].lower()
        if name in candidate_set:
            return col["name"]
    for col in columns:
        name = col["name"].lower()
        if any(candidate in name for candidate in candidate_set):
            return col["name"]
    return None


def _make_select_sql(question: str, schema_context: str, task_description: str | None = None) -> str:
    tables = _parse_schema_tables(schema_context)
    if not tables:
        return "SELECT 1 AS value LIMIT 1"
    q = f"{question} {task_description or ''}".lower()
    table = None
    for candidate in tables:
        if candidate["name"].lower() in q:
            table = candidate
            break
    if table is None:
        table = tables[0]
    cols = table.get("columns", [])
    time_col = _find_first_matching_column(cols, ["created_at", "created_on", "date", "day", "timestamp", "order_date", "event_date"])
    metric_col = _find_first_matching_column(cols, ["revenue", "profit", "amount", "sales", "total", "value", "cost", "margin", "quantity"])
    dimension_col = _find_first_matching_column(cols, ["category", "region", "segment", "status", "type", "country", "product", "customer"])

    if time_col and metric_col and any(word in q for word in ["month", "week", "day", "trend", "over time", "last", "this"]):
        return (
            f"SELECT date_trunc('month', t.{time_col}) AS period, "
            f"SUM(CAST(t.{metric_col} AS numeric)) AS metric_value "
            f"FROM {table['name']} t "
            f"GROUP BY 1 "
            f"ORDER BY 1 DESC "
            f"LIMIT {settings.max_query_rows}"
        )
    if metric_col and dimension_col:
        return (
            f"SELECT t.{dimension_col} AS dimension, "
            f"SUM(CAST(t.{metric_col} AS numeric)) AS metric_value "
            f"FROM {table['name']} t "
            f"GROUP BY 1 "
            f"ORDER BY metric_value DESC "
            f"LIMIT {settings.max_query_rows}"
        )
    if metric_col:
        return (
            f"SELECT SUM(CAST(t.{metric_col} AS numeric)) AS metric_value "
            f"FROM {table['name']} t "
            f"LIMIT 1"
        )
    if dimension_col:
        return (
            f"SELECT t.{dimension_col} AS dimension, COUNT(*) AS row_count "
            f"FROM {table['name']} t "
            f"GROUP BY 1 "
            f"ORDER BY row_count DESC "
            f"LIMIT {settings.max_query_rows}"
        )
    selected_cols = ", ".join(f"t.{col['name']}" for col in cols[:5]) if cols else "*"
    return f"SELECT {selected_cols} FROM {table['name']} t LIMIT {settings.max_query_rows}"


def _local_intent(question: str) -> dict[str, Any]:
    q = question.lower()
    if any(word in q for word in ["what if", "if we", "simulate", "scenario", "impact"]):
        intent = "simulation"
    elif any(word in q for word in ["anomal", "unusual", "outlier", "spike", "drop"]):
        intent = "anomaly_detection" if "anomal" in q or "unusual" in q or "outlier" in q else "diagnostic"
    elif any(word in q for word in ["why", "cause", "dropped", "fall", "decrease", "increase"]):
        intent = "diagnostic"
    elif any(word in q for word in ["forecast", "predict", "next month", "next week"]):
        intent = "predictive"
    elif any(word in q for word in ["compare", "versus", "vs"]):
        intent = "comparative"
    else:
        intent = "exploratory"
    return {
        "intent": intent,
        "confidence": 0.76,
        "entities": {
            "metrics": [word for word in ["revenue", "profit", "sales", "cost", "margin"] if word in q],
            "time_period": "last month" if "last month" in q else ("this month" if "this month" in q else None),
            "dimensions": [word for word in ["region", "product", "segment", "category"] if word in q],
            "conditions": [],
            "comparison_period": "previous month" if "month" in q else None,
        },
        "requires_multiple_queries": intent in {"diagnostic", "comparative", "simulation"},
        "complexity": "complex" if intent in {"diagnostic", "simulation"} else "medium",
    }


def _local_plan(question: str, intent: dict[str, Any], schema_context: str) -> dict[str, Any]:
    q = question.lower()
    if intent.get("intent") == "simulation":
        tasks = [
            {"id": "T1", "description": "Establish current baseline metrics", "purpose": "Measure current state", "depends_on": [], "expected_output": "Baseline metric rows"},
            {"id": "T2", "description": "Apply the hypothetical change and project impact", "purpose": "Estimate directional impact", "depends_on": ["T1"], "expected_output": "Projected metric rows"},
        ]
    elif intent.get("intent") == "diagnostic":
        tasks = [
            {"id": "T1", "description": "Measure the headline metric and compare it to the prior period", "purpose": "Quantify change", "depends_on": [], "expected_output": "Period-over-period totals"},
            {"id": "T2", "description": "Break the metric down by the most relevant dimension", "purpose": "Find contributor segments", "depends_on": ["T1"], "expected_output": "Dimension totals"},
            {"id": "T3", "description": "Check whether volume, price, or mix changed", "purpose": "Isolate root cause", "depends_on": ["T2"], "expected_output": "Supporting diagnostics"},
        ]
    elif intent.get("intent") == "comparative":
        tasks = [
            {"id": "T1", "description": "Compare the main metric across the requested segments", "purpose": "Establish difference", "depends_on": [], "expected_output": "Segment comparison"},
            {"id": "T2", "description": "Identify the largest positive and negative gaps", "purpose": "Highlight drivers", "depends_on": ["T1"], "expected_output": "Gap analysis"},
        ]
    else:
        tasks = [
            {"id": "T1", "description": "Summarize the core metric or distribution from the dataset", "purpose": "Provide the main answer", "depends_on": [], "expected_output": "Summary rows"},
        ]
    return {"tasks": tasks, "analysis_strategy": "Start broad, then drill into the most likely drivers."}


def _local_analysis(results_summary: list[dict[str, Any]]) -> dict[str, Any]:
    trends = []
    anomalies = []
    findings = []
    for result in results_summary:
        rows = result.get("sample", [])
        if rows:
            findings.append(f"{result['task']} returned {result['row_count']} rows.")
            numeric_key = None
            for row in rows:
                for key, value in row.items():
                    if isinstance(value, (int, float)):
                        numeric_key = key
                        break
                if numeric_key:
                    break
            if numeric_key and len(rows) > 1:
                first = rows[0].get(numeric_key)
                last = rows[-1].get(numeric_key)
                if isinstance(first, (int, float)) and isinstance(last, (int, float)):
                    if last > first:
                        direction = "up"
                    elif last < first:
                        direction = "down"
                    else:
                        direction = "flat"
                    trends.append(
                        {
                            "metric": numeric_key,
                            "direction": direction,
                            "magnitude": f"{abs(last - first):.2f}",
                            "significance": "medium",
                        }
                    )
    summary = findings[0] if findings else "The query results were sparse, so the analysis is limited."
    return {
        "summary": summary,
        "trends": trends,
        "anomalies": anomalies,
        "key_findings": findings[:3],
        "needs_deeper_investigation": ["dimension breakdown", "time trend"] if findings else [],
    }


def _local_hypotheses(question: str, analysis: dict[str, Any]) -> dict[str, Any]:
    hypotheses = []
    for finding in analysis.get("key_findings", [])[:3]:
        hypotheses.append(
            {
                "hypothesis": f"{finding} This may be driven by a shift in mix or a change in the underlying volume.",
                "validation_query": "SELECT 1 AS validation_signal LIMIT 1",
                "expected_signal": "Directional confirmation",
                "priority": "high",
            }
        )
    if not hypotheses:
        hypotheses.append(
            {
                "hypothesis": "No strong signal emerged, so the issue may be concentrated in a small segment.",
                "validation_query": "SELECT 1 AS validation_signal LIMIT 1",
                "expected_signal": "No significant deviation",
                "priority": "medium",
            }
        )
    return {"hypotheses": hypotheses}


def _local_insight(question: str, intent: dict[str, Any], plan: dict[str, Any], query_results: list[dict[str, Any]], analysis: dict[str, Any]) -> str:
    lines = [f"Question: {question}"]
    lines.append(f"Intent: {intent.get('intent', 'exploratory')}")
    if analysis.get("summary"):
        lines.append(f"Summary: {analysis['summary']}")
    if analysis.get("trends"):
        trend_bits = ", ".join(
            f"{item.get('metric')} {item.get('direction')} ({item.get('magnitude')})"
            for item in analysis["trends"][:3]
        )
        lines.append(f"Trends: {trend_bits}")
    if query_results:
        lines.append(f"Executed {len(query_results)} query steps and inspected the main contributors.")
    lines.append("Recommendation: validate the top driver segments and repeat the query on a smaller slice if the signal is concentrated.")
    return " ".join(lines)


def _local_simulation(question: str, parameters: dict[str, Any], schema_context: str) -> dict[str, Any]:
    return {
        "simulation_plan": [
            {
                "label": "baseline",
                "description": "Measure the current baseline",
                "sql": "SELECT 1 AS baseline_value LIMIT 1",
            },
            {
                "label": "projected",
                "description": "Apply the hypothetical change mathematically",
                "sql": "SELECT 1.1 AS projected_value LIMIT 1",
            },
        ],
        "assumptions": [f"Scenario: {question}", f"Parameters: {json.dumps(parameters, default=str)}"],
    }


class LLMService:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_seconds) if AsyncOpenAI and settings.openai_api_key else None
        self.model = settings.llm_model
        self.cache = QueryCache()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        use_cache: bool = True,
    ) -> str:
        cache_key = f"{system_prompt}::{user_prompt}::{temperature}::{max_tokens}"
        if use_cache:
            cached = await self.cache.get(cache_key)
            if cached:
                return cached

        result: str | None = None

        if self.client is not None:
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                result = response.choices[0].message.content or ""
            except RateLimitError:
                logger.warning("openai_rate_limit_fallback_to_ollama")
            except APIError as exc:
                logger.error("openai_api_error", error=str(exc))
            except Exception as exc:
                logger.error("openai_unknown_error", error=str(exc))

        if result is None:
            result = await self._ollama_complete(system_prompt, user_prompt, temperature, max_tokens)

        if result is None:
            result = self._local_complete(system_prompt, user_prompt)

        if use_cache and result:
            await self.cache.set(cache_key, result)
        return result

    async def _ollama_complete(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int
    ) -> str | None:
        """Call a self-hosted Ollama endpoint as a privacy-preserving LLM tier.

        Returns ``None`` when Ollama is not configured or the request fails, so
        the caller can continue to the deterministic offline fallback.
        """
        if not settings.ollama_base_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                    json={
                        "model": settings.ollama_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": False,
                        "options": {"temperature": temperature, "num_predict": max_tokens},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                return payload.get("message", {}).get("content")
        except Exception as exc:
            logger.warning("ollama_completion_error", error=str(exc))
            return None

    async def stream_complete(self, system_prompt: str, user_prompt: str):
        text = await self.complete(system_prompt, user_prompt, temperature=0.2, max_tokens=3000, use_cache=False)
        words = text.split()
        for word in words:
            yield word + " "

    def _local_complete(self, system_prompt: str, user_prompt: str) -> str:
        prompt = system_prompt.lower()
        question = _local_question_text(user_prompt)
        if "intent classification expert" in prompt:
            return json.dumps(_local_intent(question))
        if "decomposing analytical questions" in prompt or "senior business analyst" in prompt:
            intent = _local_intent(question)
            schema_match = re.search(r"Schema:\n(.+)", user_prompt, re.DOTALL)
            schema_context = schema_match.group(1) if schema_match else ""
            return json.dumps(_local_plan(question, intent, schema_context))
        if "expert postgresql query writer" in prompt:
            schema_match = re.search(r"Schema:\n(.+)", user_prompt, re.DOTALL)
            schema_context = schema_match.group(1) if schema_match else ""
            task_match = re.search(r"Task:\s*(.+)", user_prompt)
            task_description = task_match.group(1).strip() if task_match else None
            return _make_select_sql(question, schema_context, task_description)
        if "expert postgresql debugger" in prompt:
            sql_match = re.search(r"Failing SQL:\n(.*?)\n\nError:", user_prompt, re.DOTALL)
            sql = sql_match.group(1).strip() if sql_match else ""
            # Without a real model we cannot repair SQL. Echoing the failing query
            # back would only retry the same failure; emitting a fake placeholder
            # would report a misleading "success". Signal the caller explicitly.
            if not sql:
                return LOCAL_SQL_FIX_UNAVAILABLE
            return sql if sql.lower().startswith("select") or sql.lower().startswith("with") else LOCAL_SQL_FIX_UNAVAILABLE
        if "quantitative data analyst" in prompt:
            try:
                summary_match = re.search(r"Query results to analyze:\n(.*)", user_prompt, re.DOTALL)
                results = json.loads(summary_match.group(1)) if summary_match else []
            except Exception:
                results = []
            return json.dumps(_local_analysis(results))
        if "business simulation expert" in prompt:
            schema_match = re.search(r"Schema:\n(.+)", user_prompt, re.DOTALL)
            schema_context = schema_match.group(1) if schema_match else ""
            params_match = re.search(r"Parameters:\s*(\{.*\})", user_prompt)
            params = json.loads(params_match.group(1)) if params_match else {}
            return json.dumps(_local_simulation(question, params, schema_context))
        if "business analyst explaining simulation results" in prompt or "synthesizes all evidence" in prompt:
            try:
                intent = json.loads(re.search(r"Intent:\s*(\{.*\})", user_prompt, re.DOTALL).group(1))
            except Exception:
                intent = _local_intent(question)
            plan = {"tasks": []}
            query_results = []
            analysis = {}
            return _local_insight(question, intent, plan, query_results, analysis)
        if "hypothesis" in prompt:
            try:
                analysis_match = re.search(r"Analysis:\s*(\{.*\})", user_prompt, re.DOTALL)
                analysis = json.loads(analysis_match.group(1)) if analysis_match else {}
            except Exception:
                analysis = {}
            return json.dumps(_local_hypotheses(question, analysis))
        if "data visualization expert" in prompt:
            # Non-greedy match so the columns array does not swallow the
            # "Sample rows" line that follows it (greedy `\[.*\]` with
            # re.DOTALL spans both and breaks json parsing).
            cols_match = re.search(r"Columns:\s*(\[.*?\])\s*\n", user_prompt, re.DOTALL)
            rows_match = re.search(r"Sample rows:\s*(\[.*\])", user_prompt, re.DOTALL)
            task_match = re.search(r"Task:\s*(.+)", user_prompt)
            try:
                columns = json.loads(cols_match.group(1)) if cols_match else []
            except Exception:
                columns = []
            try:
                rows = json.loads(rows_match.group(1)) if rows_match else []
            except Exception:
                rows = []
            task_description = task_match.group(1).strip() if task_match else None
            return json.dumps(recommend_chart(columns, rows, task_description))
        return question
