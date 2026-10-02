"""Python code execution node: run data analysis code in a secure subprocess

Security measures:
  - subprocess + timeout (30s)
  - restricted module whitelist (no os/subprocess/socket)
  - data passed via temp file (no f-string injection)
  - AST-based code validation
  - injection pattern detection

Input: state["_last_result"] (query result)
Output: execution result (chart/table/text)
"""
import ast
import json
import os
import subprocess
import sys
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from app.core.path_guard import safe_temp_path
from langchain_core.messages import HumanMessage


ALLOWED_MODULES = {
    "pandas", "numpy", "math", "statistics", "json", "datetime",
    "collections", "itertools", "functools", "operator",
}

# Patterns that indicate injection attempts in LLM-generated code
INJECTION_PATTERNS = [
    "'''", '"""', "__import__(", "compile(", "exec(", "eval(",
    "importlib", "builtins", "getattr(", "subclasses", "globals(",
    "locals(", "vars(", "setattr(", "__class__", "__globals__",
    "__builtins__", "__base__",
]

# Direct builtin calls that must never appear (data arrives pre-loaded;
# there is no legitimate reason for generated analysis code to touch files
# or process state).
FORBIDDEN_BUILTIN_CALLS = {
    "open", "exec", "eval", "compile", "__import__", "input",
    "globals", "locals", "vars", "setattr", "delattr", "breakpoint",
}


def validate_code(code: str) -> tuple:
    """Security check for Python code via AST + pattern scanning.

    Layer 1: substring patterns (fast, catches obfuscation leftovers).
    Layer 2: AST walk - import WHITELIST (not just denylist), builtin
    call denylist, and dunder attribute access (classic sandbox escapes
    via .__class__.__subclasses__() chains).
    """
    for pattern in INJECTION_PATTERNS:
        if pattern in code:
            return False, f"Injection pattern detected: {repr(pattern)}"

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod not in ALLOWED_MODULES:
                    return False, f"Module not in whitelist: {mod}"
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod not in ALLOWED_MODULES:
                return False, f"Module not in whitelist: {mod}"
        if isinstance(node, ast.Attribute):
            # dunder attribute access = sandbox escape hatch
            # (.__class__, .__globals__, .__subclasses__, ...)
            if node.attr.startswith("__") and not node.attr.endswith("__"):
                pass  # name-mangled private attrs are harmless
            elif node.attr.startswith("__") and node.attr.endswith("__"):
                if node.attr not in ("__len__", "__iter__", "__next__",
                                     "__str__", "__repr__", "__enter__",
                                     "__exit__", "__call__", "__getitem__",
                                     "__setitem__", "__add__", "__sub__",
                                     "__mul__", "__div__", "__lt__", "__gt__",
                                     "__eq__", "__contains__", "__format__"):
                    return False, f"Dunder attribute access blocked: {node.attr}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in ("system", "popen", "exec", "eval", "open"):
                    if isinstance(func.value, ast.Name):
                        if func.value.id in ("os", "subprocess", "pathlib", "io"):
                            return False, f"Forbidden call to {func.value.id}.{func.attr}"
            if isinstance(func, ast.Name):
                if func.id in FORBIDDEN_BUILTIN_CALLS:
                    return False, f"Forbidden builtin call: {func.id}"

    return True, "OK"


async def code_executor(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Python code execution node.

    Flow:
      1. LLM generates Python analysis code based on query results
      2. Security check (AST + injection patterns)
      3. Subprocess execution (30s timeout) with data via temp file
      4. Return result
    """
    writer = runtime.stream_writer
    from app.agent.nodes._analysis_gate import analysis_wanted
    if not analysis_wanted(state):
        logger.info("code_executor skipped (fast lane)")
        writer({"stage": "Python Analysis (skipped)"})
        return {"code_result": None}
    writer({"stage": "Python Analysis"})
    try:
        data = state.get("_last_result", [])
        query = state.get("query", "")
        # M10 FR3: use the full spilled result when available.
        try:
            from app.services.spill_store import resolve_full_rows
            meta_repo = runtime.context.get("meta_doris_repository")
            if state.get("_spill_id") and meta_repo is not None:
                data = await resolve_full_rows(state, meta_repo.session)
        except Exception as e:
            logger.debug(f"spill resolve in code_executor skipped: {e}")

        if not data or not isinstance(data, list):
            return {"code_result": None}

        data_str = json.dumps(data[:20], ensure_ascii=False, default=str)
        prompt = f"""You are a data analyst. Generate Python analysis code based on the following data.

User question: {query}
Data (JSON): {data_str}

Requirements:
1. Use pandas to process data
2. Only use pandas/numpy/math/statistics modules
3. Output a result variable (list[dict] or dict)
4. Pure code, no explanation

```python
import pandas as pd
# your code
result = ...
```"""

        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        code = resp.content.strip()

        if "```" in code:
            parts = code.split("```")
            code = parts[1] if len(parts) > 1 else parts[0]
            if code.startswith("python\n"):
                code = code[7:]

        is_safe, msg = validate_code(code)
        if not is_safe:
            logger.warning(f"Python code security check failed: {msg}")
            return {"code_result": {"error": msg}}

        result = _execute_python(code, data_str)

        logger.info(f"Python execution done: {str(result)[:100]}")
        writer({"code_result": result})
        return {"code_result": result}
    except Exception as e:
        logger.error(f"Python execution error: {e}")
        return {"code_result": {"error": str(e)}}


def _execute_python(code: str, data_json: str, timeout: int = 30) -> dict:
    """Execute Python code in a subprocess.

    Data is written to a temp JSON file to prevent triple-quote injection.
    The wrapper reads data from the file, eliminating f-string escape risk.
    """
    import textwrap as _tw
    indented = _tw.indent(code, "    ")

    data_path = safe_temp_path(suffix="_data.json")
    script_path = safe_temp_path(suffix=".py")

    with open(data_path, "w", encoding="utf-8") as f:
        f.write(data_json)

    # Use raw string + escaped path to prevent backslash issues
    safe_path = data_path.replace("\\", "\\\\")
    wrapper = f'\
import json, sys\n\
with open(r"{safe_path}", "r", encoding="utf-8") as _f:\n\
    _data = json.load(_f)\n\
try:\n\
{indented}\n\
    if "result" not in dir():\n\
        result = {{"error": "code did not define result variable"}}\n\
    print(json.dumps(result, default=str, ensure_ascii=False))\n\
except Exception as e:\n\
    print(json.dumps({{"error": str(e)}}))\n\
'

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(wrapper)

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )

        if proc.returncode != 0:
            return {"error": f"Execution failed: {proc.stderr[:200]}"}

        output = proc.stdout.strip()
        if output:
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return {"text_output": output[:500]}
        return {"error": "No output"}
    except subprocess.TimeoutExpired:
        return {"error": f"Execution timeout ({timeout}s)"}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
        try:
            os.unlink(data_path)
        except OSError:
            pass
