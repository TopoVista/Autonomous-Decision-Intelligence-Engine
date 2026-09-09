"""Specialist registry: maps agent archetypes to declared capabilities.

All specialist implementations are pure Python with zero heavy dependencies,
safe for the Render 512 MB free tier.
"""

from __future__ import annotations

from app.agents.chart_recommender_agent import ChartRecommenderAgent
from app.agents.hypothesis_agent import HypothesisAgent
from app.agents.insight_agent import InsightAgent
from app.agents.intent_agent import IntentAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.result_analyzer_agent import ResultAnalyzerAgent
from app.agents.simulation_agent import SimulationAgent
from app.agents.sql_generator_agent import SQLGeneratorAgent
from app.core.registry import Specialist, SpecialistRegistry
from app.specialists.anomaly_specialist import AnomalySpecialist
from app.specialists.causal_specialist import CausalSpecialist
from app.specialists.dashboard_specialist import DashboardSpecialist
from app.specialists.ml_specialist import MLSpecialist
from app.specialists.nlp_specialist import NLPSpecialist
from app.specialists.timeseries_specialist import TimeSeriesSpecialist

specialist_registry = SpecialistRegistry()

INTENT_ROUTING: dict[str, list[str]] = {
    "diagnostic": [
        "sql_database_analyst",
        "exploratory_data_analyst",
        "hypothesis_generator",
        "insight_synthesizer",
        "anomaly_advanced",
    ],
    "exploratory": [
        "sql_database_analyst",
        "exploratory_data_analyst",
        "insight_synthesizer",
        "nlp_text_analyst",
    ],
    "comparative": [
        "sql_database_analyst",
        "exploratory_data_analyst",
        "insight_synthesizer",
    ],
    "anomaly_detection": [
        "sql_database_analyst",
        "anomaly_detector",
        "anomaly_advanced",
        "insight_synthesizer",
    ],
    "predictive": [
        "sql_database_analyst",
        "time_series_forecaster",
        "ml_scientist",
        "insight_synthesizer",
    ],
    "simulation": ["business_simulator"],
    "nlp": [
        "nlp_text_analyst",
        "insight_synthesizer",
    ],
    "ml": [
        "sql_database_analyst",
        "ml_scientist",
        "insight_synthesizer",
    ],
    "causal": [
        "sql_database_analyst",
        "causal_analyst",
        "insight_synthesizer",
    ],
    "dashboard": [
        "sql_database_analyst",
        "exploratory_data_analyst",
        "dashboard_expert",
        "insight_synthesizer",
    ],
    "timeseries": [
        "sql_database_analyst",
        "time_series_forecaster",
        "insight_synthesizer",
    ],
}

# Maps specialist id → implementation class (None = uses pipeline agent)
_SPECIALIST_CLASSES: dict[str, type | None] = {
    # Pipeline agents need an LLM service and orchestration context. They are
    # available through /query, but deliberately not exposed as direct RPC.
    "intent_classifier": None,
    "planner": None,
    "sql_database_analyst": None,
    "exploratory_data_analyst": None,
    "hypothesis_generator": None,
    "insight_synthesizer": None,
    "anomaly_detector": None,          # handled by AnomalyDetector tool in pipeline
    "anomaly_advanced": AnomalySpecialist,
    "business_simulator": None,
    "document_intelligence_analyst": None,
    "nlp_text_analyst": NLPSpecialist,
    "time_series_forecaster": TimeSeriesSpecialist,
    "ml_scientist": MLSpecialist,
    "causal_analyst": CausalSpecialist,
    "dashboard_expert": DashboardSpecialist,
}


def _register_all() -> SpecialistRegistry:
    _specs = [
        Specialist(
            id="intent_classifier",
            name="Intent Classifier",
            description="Classifies user questions into analytical intents and extracts entities.",
            capabilities=["intent_classification"],
            supported_data_types=["tabular", "time_series", "text"],
            tools=["llm_service"],
            available=True,
        ),
        Specialist(
            id="planner",
            name="Task Planner",
            description="Decomposes analytical questions into SQL-executable sub-tasks.",
            capabilities=["task_planning"],
            supported_data_types=["tabular", "time_series"],
            tools=["llm_service", "sql_validator"],
            available=True,
        ),
        Specialist(
            id="sql_database_analyst",
            name="SQL Database Analyst",
            description="Generates schema-grounded NL→SQL, executes read-only queries, self-repairs.",
            capabilities=["sql_generation", "sql_execution", "sql_self_repair"],
            supported_data_types=["tabular", "time_series", "geospatial", "text"],
            tools=["schema_inspector", "sql_executor", "sql_validator"],
            available=True,
        ),
        Specialist(
            id="exploratory_data_analyst",
            name="Exploratory Data Analyst",
            description="Summarizes result distributions, identifies trends, recommends charts.",
            capabilities=["statistical_analysis", "anomaly_detection", "chart_recommendation"],
            supported_data_types=["tabular", "time_series", "geospatial"],
            tools=["anomaly_detector", "chart_recommender"],
            available=True,
        ),
        Specialist(
            id="hypothesis_generator",
            name="Hypothesis Generator",
            description="Generates testable root-cause hypotheses with validation SQL.",
            capabilities=["hypothesis_generation", "hypothesis_validation"],
            supported_data_types=["tabular", "time_series"],
            tools=["sql_generator", "sql_executor"],
            available=True,
        ),
        Specialist(
            id="insight_synthesizer",
            name="Insight Synthesizer",
            description="Synthesises all evidence into a concise, number-aware narrative.",
            capabilities=["narrative_synthesis", "insight_generation"],
            supported_data_types=["tabular", "time_series", "text", "geospatial"],
            tools=["llm_service"],
            available=True,
        ),
        Specialist(
            id="anomaly_detector",
            name="Anomaly Detector (Basic)",
            description="Detects z-score and IQR outliers in numeric result columns.",
            capabilities=["anomaly_detection"],
            supported_data_types=["tabular", "time_series"],
            tools=["anomaly_detector"],
            available=True,
        ),
        Specialist(
            id="anomaly_advanced",
            name="Anomaly Specialist (Advanced)",
            description="Isolation-forest heuristic + IQR + z-score anomaly detection on rows.",
            capabilities=["anomaly_detection", "fraud_detection"],
            supported_data_types=["tabular", "time_series"],
            tools=["anomaly_specialist"],
            available=True,
        ),
        Specialist(
            id="business_simulator",
            name="Business Simulator",
            description="Runs what-if simulation scenarios with projected KPI impact.",
            capabilities=["simulation", "forecasting"],
            supported_data_types=["tabular", "time_series"],
            tools=["schema_inspector", "sql_executor"],
            available=True,
        ),
        Specialist(
            id="document_intelligence_analyst",
            name="Document Intelligence Analyst",
            description="Parses and queries uploaded documents (PDF/TXT/MD) via RAG.",
            capabilities=["document_ingestion", "document_retrieval", "document_qa"],
            supported_data_types=["document", "pdf", "text"],
            tools=["vector_store", "document_parser"],
            available=True,
        ),
        Specialist(
            id="nlp_text_analyst",
            name="NLP / Text Analyst",
            description=(
                "Pure-Python sentiment analysis, keyword extraction, entity recognition, "
                "and extractive summarization. No external ML library required."
            ),
            capabilities=["sentiment_analysis", "entity_extraction", "text_summarisation", "keyword_extraction"],
            supported_data_types=["text"],
            tools=["nlp_specialist"],
            available=True,
        ),
        Specialist(
            id="time_series_forecaster",
            name="Time-Series Forecaster",
            description=(
                "Linear-regression forecasting, exponential smoothing, trend/seasonality "
                "detection, change-point detection — all pure Python, zero external deps."
            ),
            capabilities=["forecasting", "trend_analysis", "seasonality_decomposition", "change_point_detection"],
            supported_data_types=["time_series"],
            tools=["timeseries_specialist"],
            available=True,
        ),
        Specialist(
            id="ml_scientist",
            name="Machine Learning Scientist",
            description=(
                "Linear regression, correlation-based feature importance — pure Python, "
                "suitable for quick model exploration without heavy ML libraries."
            ),
            capabilities=["model_training", "feature_importance", "prediction"],
            supported_data_types=["tabular", "time_series"],
            tools=["ml_specialist"],
            available=True,
        ),
        Specialist(
            id="causal_analyst",
            name="Causal Analyst",
            description=(
                "Difference-in-means causal effect estimation and DAG generation "
                "— pure Python, no DoWhy/EconML dependency."
            ),
            capabilities=["causal_inference", "dag_discovery", "effect_estimation"],
            supported_data_types=["tabular"],
            tools=["causal_specialist"],
            available=True,
        ),
        Specialist(
            id="dashboard_expert",
            name="Dashboard Expert",
            description=(
                "Assembles multi-panel dashboard descriptors from query results; "
                "generates rule-based narrative summaries without LLM calls."
            ),
            capabilities=["dashboard_generation", "dashboard_narration", "layout_suggestion"],
            supported_data_types=["tabular", "time_series"],
            tools=["dashboard_specialist"],
            available=True,
        ),
    ]

    for spec in _specs:
        specialist_registry.register(spec, overwrite=True)
        cls = _SPECIALIST_CLASSES.get(spec.id)
        if cls is not None:
            specialist_registry._classes[spec.id] = cls

    return specialist_registry


_register_all()


def get_specialist_class(specialist_id: str) -> type | None:
    return _SPECIALIST_CLASSES.get(specialist_id)


def get_specialists_for_intent(intent: str) -> list[str]:
    return INTENT_ROUTING.get(
        intent,
        ["sql_database_analyst", "exploratory_data_analyst", "insight_synthesizer"],
    )


__all__ = [
    "specialist_registry",
    "INTENT_ROUTING",
    "get_specialist_class",
    "get_specialists_for_intent",
]
