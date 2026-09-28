import os

# Test runs must never send traces to the real Langfuse project or append to the real cost log
# (agents/business_analyst/observability.py, cost_log.py).
os.environ.setdefault("LANGFUSE_TRACING_ENABLED", "false")
os.environ.setdefault("BA_COST_LOG_PATH", "")
