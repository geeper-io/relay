import os

# Use SQLite in-memory for tests
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PROXY_MASTER_KEY", "test-master-key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake")
os.environ.setdefault("PII__ENABLED", "true")
os.environ.setdefault("RAG__ENABLED", "false")  # disable RAG in tests by default
