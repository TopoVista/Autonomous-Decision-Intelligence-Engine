from __future__ import annotations

import time
from typing import AsyncGenerator

import structlog

from app.agents.chart_recommender_agent import ChartRecommenderAgent
from app.agents.hypothesis_agent import HypothesisAgent
from app.agents.insight_agent import InsightAgent
from app.agents.intent_agent import IntentAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.result_analyzer_agent import ResultAnalyzerAgent
from app.agents.sql_generator_agent import SQLGeneratorAgent
from app.core.artifacts import default_artifact_store
from app.memory.session_memory import SessionMemory
from app.memory.vector_memory import VectorMemory
from app.services.llm_service import LLMService
from app.config import get_settings
from app.specialists import get_specialists_for_intent, specialist_registry
from app.tools.anomaly_detector import AnomalyDetector
from app.tools.schema_inspector import SchemaInspector
from app.tools.sql_executor import SQLExecutor

logger = structlog.get_logger()


class AgentPipeline:
    def __init__(self):
        self.llm = LLMService()
        self.intent_agent = IntentAgent(self.llm)
        self.planner = PlannerAgent(self.llm)
        self.sql_gen = SQLGeneratorAgent(self.llm)
        self.result_analyzer = ResultAnalyzerAgent(self.llm)
        self.chart_recommender = ChartRecommenderAgent(self.llm)
        self.hypothesis_agent = HypothesisAgent(self.llm)
        self.insight_agent = InsightAgent(self.llm)
        self.executor = SQLExecutor()
        self.schema_inspector = SchemaInspector()

    async def run(
        self,
        user_question: str,
        connection_string: str,
        session_id: str,
        user_id: str,
        connection_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        start_time = time.monotonic()
        yield {"type": "step", "data": {"step": "schema_inspection", "message": "Reading database schema..."}}
        schema = await self.schema_inspector.get_schema(connection_string)
        schema_str = self.schema_inspector.to_prompt_string(schema)

        memory = SessionMemory(session_id)
        history = await memory.get_history()
        vector_memory = VectorMemory(user_id)
        similar_past = await vector_memory.search_similar(user_question, limit=3)

        yield {"type": "step", "data": {"step": "intent_classification", "message": "Understanding your question..."}}
        intent = await self.intent_agent.run(user_question, schema_str)
        if not isinstance(intent, dict):
            logger.warning("invalid_intent_agent_result")
            intent = {"intent": "exploratory", "confidence": 0.0, "entities": {}}
        yield {"type": "intent", "data": intent}

        # Intent-based routing — look up which specialists handle this intent.
        specialist_ids = get_specialists_for_intent(intent.get("intent", "exploratory"))
        specialists_info = []
        for spec_id in specialist_ids:
            spec = specialist_registry.metadata(spec_id)
            if spec is not None:
                specialists_info.append({
                    "id": spec_id,
                    "name": spec.name,
                    "capabilities": spec.capabilities,
                    "available": spec.available,
                })
        yield {
            "type": "specialist_routing",
            "data": {
                "intent": intent.get("intent"),
                "specialists": specialists_info,
            },
        }

        yield {"type": "step", "data": {"step": "task_planning", "message": "Breaking down into sub-questions..."}}
        plan = await self.planner.run(user_question, intent, schema_str, history)
        if not isinstance(plan, dict):
            logger.warning("invalid_planner_result")
            plan = {}
        raw_tasks = plan.get("tasks")
        if not isinstance(raw_tasks, list):
            raw_tasks = []
        tasks = [
            {**task, "id": str(task.get("id") or f"T{index}"), "description": str(task["description"])}
            for index, task in enumerate(raw_tasks, start=1)
            if isinstance(task, dict) and isinstance(task.get("description"), str) and task["description"].strip()
        ]
        if not tasks:
            tasks = [{"id": "T1", "description": "Summarize the available data for the question."}]
        plan["tasks"] = tasks
        yield {"type": "plan", "data": plan}

        query_results = []
        settings = get_settings()
        tasks = tasks[: settings.agent_max_iterations]
        if len(plan["tasks"]) > len(tasks):
            logger.warning("agent_plan_truncated", requested_tasks=len(plan["tasks"]), max_tasks=settings.agent_max_iterations)
        for task in tasks:
            yield {
                "type": "step",
                "data": {"step": "sql_generation", "message": f"Generating query: {task['description'][:60]}..."},
            }
            sql_result = await self.sql_gen.run(task, schema_str, query_results)
            if not isinstance(sql_result, dict):
                sql_result = {"sql": None, "task_description": task["description"]}
            sql = sql_result.get("sql")
            result = None
            for attempt in range(min(3, settings.agent_max_iterations)):
                if sql is None:
                    result = {
                        **sql_result,
                        "task_id": task["id"],
                        "success": False,
                        "rows": [],
                        "columns": [],
                        "row_count": 0,
                        "error": "Unable to produce a valid query for this step after repeated attempts. "
                        "Please rephrase the question or verify the table/column names against the schema.",
                    }
                    break
                exec_result = await self.executor.execute(connection_string, sql)
                if exec_result["success"]:
                    result = {**sql_result, "sql": sql, "task_id": task["id"], **exec_result}
                    if result.get("columns") and result.get("rows"):
                        result["chart_spec"] = await self.chart_recommender.run(
                            result["columns"],
                            result["rows"],
                            task_description=result.get("task_description"),
                        )
                    break
                if attempt < min(3, settings.agent_max_iterations) - 1:
                    yield {
                        "type": "step",
                        "data": {"step": "sql_correction", "message": f"Correcting SQL error (attempt {attempt + 2})..."},
                    }
                    sql = await self.sql_gen.fix_sql(sql, exec_result["error"], schema_str)
                else:
                    result = {**sql_result, "sql": sql, "task_id": task["id"], "success": False, "rows": [], "columns": [], "row_count": 0, "error": exec_result["error"]}
            yield {
                "type": "query_result",
                "data": {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "sql": result.get("sql"),
                    "rows": result.get("rows", [])[:5],
                    "columns": result.get("columns", []),
                    "chart_spec": result.get("chart_spec"),
                    "success": result.get("success"),
                    "row_count": result.get("row_count", 0),
                    "error": result.get("error"),
                },
            }
            query_results.append(result)

        yield {"type": "step", "data": {"step": "result_analysis", "message": "Analyzing patterns and anomalies..."}}
        analysis = await self.result_analyzer.run(query_results)
        yield {"type": "analysis", "data": analysis}

        # ── Specialist routing ───────────────────────────────────────────────
        # Run activated pure-Python specialists based on intent.
        # All calls are in-process; safe on 512 MB Render free tier.
        specialist_results: list[dict] = []
        _intent_str = intent.get("intent", "exploratory")

        try:
            # Time-series / predictive: run on every numeric column found in results
            if _intent_str in ("predictive", "timeseries", "diagnostic"):
                from app.specialists.timeseries_specialist import TimeSeriesSpecialist
                ts_spec = TimeSeriesSpecialist()
                for result in query_results:
                    if not result.get("success") or not result.get("rows"):
                        continue
                    rows = result["rows"][: settings.max_specialist_rows]
                    cols = result.get("columns", [])
                    for col in cols[: settings.max_specialist_columns]:
                        values: list[float] = []
                        for row in rows:
                            try:
                                values.append(float(row[col]))
                            except (TypeError, ValueError, KeyError):
                                pass
                        if len(values) >= 3:
                            yield {"type": "step", "data": {"step": "specialist_timeseries", "message": f"Time-series analysis on '{col}'..."}}
                            ts_result = await ts_spec.full_analysis(values, column_name=col)
                            specialist_results.append({"specialist": "time_series_forecaster", "column": col, "result": ts_result})
                            yield {"type": "specialist_result", "data": {"specialist": "time_series_forecaster", "column": col, "result": ts_result}}
        except Exception as _spec_exc:
            logger.warning("time_series_specialist_error", error=str(_spec_exc))

        try:
            # NLP: run on text-heavy result columns when intent is exploratory or nlp
            if _intent_str in ("exploratory", "nlp"):
                from app.specialists.nlp_specialist import NLPSpecialist
                nlp_spec = NLPSpecialist()
                for result in query_results:
                    if not result.get("success") or not result.get("rows"):
                        continue
                    rows = result["rows"][: settings.max_specialist_rows]
                    cols = result.get("columns", [])
                    for col in cols[: settings.max_specialist_columns]:
                        text_vals: list[str] = []
                        for row in rows:
                            v = row.get(col)
                            if isinstance(v, str) and len(v) > 10:
                                text_vals.append(v)
                        if len(text_vals) >= 2:
                            yield {"type": "step", "data": {"step": "specialist_nlp", "message": f"Text analysis on '{col}'..."}}
                            nlp_result = await nlp_spec.analyze_column(text_vals, column_name=col)
                            specialist_results.append({"specialist": "nlp_text_analyst", "column": col, "result": nlp_result})
                            yield {"type": "specialist_result", "data": {"specialist": "nlp_text_analyst", "column": col, "result": nlp_result}}
        except Exception as _spec_exc:
            logger.warning("nlp_specialist_error", error=str(_spec_exc))

        try:
            # ML: train a quick regression when intent is ml or predictive
            if _intent_str in ("ml", "predictive"):
                from app.specialists.ml_specialist import MLSpecialist
                ml_spec = MLSpecialist()
                for result in query_results:
                    if not result.get("success") or not result.get("rows"):
                        continue
                    rows = result["rows"][: settings.max_specialist_rows]
                    cols = result.get("columns", [])
                    numeric_cols = []
                    for col in cols:
                        try:
                            float(rows[0][col])
                            numeric_cols.append(col)
                        except Exception:
                            pass
                    if len(numeric_cols) >= 2:
                        target = numeric_cols[-1]
                        features = [numeric_cols[0]]
                        yield {"type": "step", "data": {"step": "specialist_ml", "message": f"ML model: predict '{target}' from {features}..."}}
                        ml_result = await ml_spec.train_model(rows, target=target, features=features)
                        specialist_results.append({"specialist": "ml_scientist", "target": target, "features": features, "result": ml_result})
                        yield {"type": "specialist_result", "data": {"specialist": "ml_scientist", "target": target, "features": features, "result": ml_result}}
        except Exception as _spec_exc:
            logger.warning("ml_specialist_error", error=str(_spec_exc))

        try:
            # Dashboard: assemble multi-panel descriptor
            if _intent_str in ("dashboard", "exploratory"):
                from app.specialists.dashboard_specialist import DashboardSpecialist
                dash_spec = DashboardSpecialist()
                yield {"type": "step", "data": {"step": "specialist_dashboard", "message": "Assembling dashboard..."}}
                dashboard = await dash_spec.assemble_dashboard(query_results)
                specialist_results.append({"specialist": "dashboard_expert", "result": dashboard})
                yield {"type": "specialist_result", "data": {"specialist": "dashboard_expert", "result": dashboard}}
        except Exception as _spec_exc:
            logger.warning("dashboard_specialist_error", error=str(_spec_exc))
        # ── end specialist routing ───────────────────────────────────────────

        # For anomaly_detection intent, explicitly flag anomalies in each result set.
        if intent.get("intent") == "anomaly_detection":
            yield {"type": "step", "data": {"step": "anomaly_scan", "message": "Scanning result sets for anomalies..."}}
            detector = AnomalyDetector()
            anomaly_findings: list[dict] = []
            for result in query_results:
                if result.get("success") and result.get("rows") and result.get("columns"):
                    anomalies = detector.detect(result["rows"], result["columns"])
                    for a in anomalies:
                        a_copy = dict(a)
                        a_copy["task_id"] = result.get("task_id")
                        anomaly_findings.append(a_copy)
            if anomaly_findings:
                analysis.setdefault("anomaly_findings", []).extend(anomaly_findings)

        hypotheses = {"hypotheses": []}
        if intent.get("intent") == "diagnostic" and analysis.get("needs_deeper_investigation"):
            yield {"type": "step", "data": {"step": "hypothesis_generation", "message": "Generating hypotheses..."}}
            hypotheses = await self.hypothesis_agent.run(user_question, analysis, schema_str)
            for hyp in hypotheses.get("hypotheses", [])[:2]:
                yield {
                    "type": "step",
                    "data": {"step": "hypothesis_validation", "message": f"Validating: {hyp['hypothesis'][:60]}..."},
                }
                val_sql_result = await self.sql_gen.run({"id": "VAL", "description": hyp["validation_query"]}, schema_str, query_results)
                val_exec = await self.executor.execute(connection_string, val_sql_result["sql"])
                query_results.append({**val_sql_result, **val_exec, "is_validation": True})

        yield {"type": "step", "data": {"step": "insight_generation", "message": "Composing analysis..."}}
        full_insight = ""
        async for token in self.insight_agent.stream(
            user_question=user_question,
            intent=intent,
            plan=plan,
            query_results=query_results,
            analysis=analysis,
            similar_queries=similar_past,
        ):
            full_insight += token
            yield {"type": "insight_token", "data": {"token": token}}

        await memory.add_entry(user_question, full_insight)
        await vector_memory.store(user_question, full_insight, connection_id=connection_id)

        # Persist results as composable artifacts in the ArtifactStore.
        artifact_store = default_artifact_store
        for result in query_results:
            if result.get("success") and result.get("rows"):
                artifact_store.create(
                    type="table",
                    content={"columns": result.get("columns", []), "rows": result.get("rows", [])[:5]},
                    session_id=session_id,
                    source=result.get("task_description", "query"),
                    metadata={"task_id": result.get("task_id"), "sql": result.get("sql")},
                )
            if result.get("chart_spec"):
                artifact_store.create(
                    type="chart",
                    content=result["chart_spec"],
                    session_id=session_id,
                    source=result.get("task_description", "query"),
                    metadata={"task_id": result.get("task_id")},
                )
        artifact_store.create(
            type="analysis",
            content=analysis,
            session_id=session_id,
            source="result_analysis",
        )
        artifact_store.create(
            type="insight",
            content=full_insight,
            session_id=session_id,
            source="insight_generation",
        )
        artifacts_summary = [
            {"id": a.id, "type": a.type, "source": a.source, "created_at": a.created_at}
            for a in artifact_store.list(session_id=session_id)
        ]

        total_time = int((time.monotonic() - start_time) * 1000)
        yield {
            "type": "done",
            "data": {
                "execution_time_ms": total_time,
                "queries_executed": len(query_results),
                "anomalies_found": len(analysis.get("statistical_anomalies", [])),
                "final_insight": full_insight,
                "intent": intent,
                "plan": plan,
                "analysis": analysis,
                "hypotheses": hypotheses,
                "query_results": [
                    {
                        "task_id": result.get("task_id"),
                        "task_description": result.get("task_description"),
                        "sql": result.get("sql"),
                        "success": result.get("success"),
                        "row_count": result.get("row_count", 0),
                        "error": result.get("error"),
                    }
                    for result in query_results
                ],
                "artifacts": artifacts_summary,
                "specialist_results": specialist_results,
            },
        }

