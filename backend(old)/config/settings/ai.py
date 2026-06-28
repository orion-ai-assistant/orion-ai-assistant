"""
AI and Model specific settings.
Shared by API and Agent services.
"""
from common.env_helper import get_env

# --- External API Keys and tokens ---
GOOGLE_API_KEY = get_env("GOOGLE_API_KEY")
LANGSMITH_API_KEY = get_env("LANGSMITH_API_KEY")
TAVILY_API_KEY = get_env("TAVILY_API_KEY")

# --- Vertex AI (ADC) settings ---
# If enabled, Gemini requests go through Google Cloud Vertex AI using ADC credentials
# (no GOOGLE_API_KEY required).
VERTEXAI_ENABLED = get_env("VERTEXAI_ENABLED", required=False) == "true"
VERTEX_PROJECT = (
    get_env("VERTEX_PROJECT", required=False)
    or get_env("GOOGLE_CLOUD_PROJECT", required=False)
    or get_env("GCLOUD_PROJECT", required=False)
)
VERTEX_LOCATION = (
    get_env("VERTEX_LOCATION", required=False)
    or get_env("GOOGLE_CLOUD_LOCATION", required=False)
    or get_env("GOOGLE_CLOUD_REGION", required=False)
)

# --- LLM diagnostics (dev / perf A-B) ---
# ORION_LLM_DIAG=true -> call_model içinde get_model + ainvoke süreleri (INFO log)
ORION_LLM_DIAG = get_env("ORION_LLM_DIAG", required=False) == "true"
# ORION_LLM_NO_BIND_TOOLS=true -> bind_tools atlanır (tool çağrısı olmaz; sadece gecikme kökü için)
ORION_LLM_NO_BIND_TOOLS = get_env("ORION_LLM_NO_BIND_TOOLS", required=False) == "true"

