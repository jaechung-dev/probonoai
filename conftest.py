import os

# Keep test runs out of LangSmith. services/mcp/server.py calls load_dotenv(),
# which pulls LANGSMITH_TRACING=true from a local .env into the whole test
# process. load_dotenv() never overrides variables already set, so pinning
# these here, before any test module is imported, wins.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
