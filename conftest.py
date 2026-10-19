import os

# Keep test runs out of LangSmith. services/mcp/server.py calls load_dotenv(),
# which pulls LANGSMITH_TRACING=true from a local .env into the whole test
# process. load_dotenv() never overrides variables already set, so pinning
# these here, before any test module is imported, wins.
#
# Opt in for a run with PYTEST_LANGSMITH=1: loads .env up front (key, endpoint,
# project) so every test traces, not only those after the MCP import.
if os.environ.get("PYTEST_LANGSMITH") == "1":
    from dotenv import load_dotenv

    load_dotenv()
    os.environ["LANGSMITH_TRACING"] = "true"
else:
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
