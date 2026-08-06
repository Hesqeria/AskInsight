import os
import sys
from pathlib import Path

import pytest

# Allow tests to import app.*
sys.path.insert(0, str(Path(__file__).parent.parent))

# Fallback env for tests to avoid import failure when env is missing
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999/v1")
os.environ.setdefault("LLM_MODEL_NAME", "test-model")
os.environ.setdefault("DORIS_PASSWORD", "test")
os.environ.setdefault("MILVUS_PASSWORD", "test")
