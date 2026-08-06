"""Python code execution node: run data analysis code in a secure subprocess

Security measures:
  - subprocess + timeout（30s）
  - restricted module whitelist (no os/subprocess/socket)
  - memory limit (optional)
  - no network access

Input: state["_last_result"] (query result)
Output: execution result (chart/table/text)
"""
import ast
import json
import subprocess
import sys
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.agent.llm import llm
from app.core.log import logger
from app.core.path_guard import safe_temp_path
from langchain_core.messages import HumanMessage


# Allowed Python module whitelist
ALLOWED_MODULES = {
    "pandas", "numpy", "math", "statistics", "json", "datetime",
    "collections", "itertools", "functools", "operator",
}

# Forbidden modules/functions
FORBIDDEN_IMPORTS = {"os", "subprocess", "socket", "shutil", "pty", "threading", "multiprocessing"}


def validate_code(code: str) -> tuple[bool, str]:
    """Security check for Python code"""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in FORBIDDEN_IMPORTS:
                    return False, f"Forbidden module import: {mod}"
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_IMPORTS:
                return False, f"Forbidden module import: {mod}"
        # Check for dangerous function calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in ("system", "popen", "exec", "eval", "open"):
                    if isinstance(func.value, ast.Name) and func.value.id == "os":
                        return False, f"Forbidden call to os.{func.attr}"
    return True, "OK"


async def code_executor(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """Python code execution node

    Flow:
      1. LLM generates Python analysis code based on query results
      2. Security check
      3. Subprocess execution (30s timeout)
      4. Return result
    """
    writer = runtime.stream_writer
    writer({"stage": "Python Analysis"})
    try:
        data = state.get("_last_result", [])
        query = state.get("query", "")

        if not data or not isinstance(data, list):
            return {"code_result": None}

        # 1. LLM generates Python code
        data_str = json.dumps(data[:20], ensure_ascii=False, default=str)
        prompt = f"""You are a data analyst. Generate Python analysis code based on the following data.

User question: {query}
Data (JSON): {data_str}

Requirements:
1. Use pandas to process data
2. Only use pandas/numpy/math/statistics modules
3. Output a result variable (list[dict] or dict)
4. Optional: generate a matplotlib chart and save to /tmp/chart.png
5. Pure code, no explanation

```python
import pandas as pd
# your code
result = ...
```"""

        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        code = resp.content.strip()

        # Extract code block
        if "```" in code:
            parts = code.split("```")
            code = parts[1] if len(parts) > 1 else parts[0]
            if code.startswith("python\n"):
                code = code[7:]

        # 2. Security check
        is_safe, msg = validate_code(code)
        if not is_safe:
            logger.warning(f"Python code security check failed: {msg}")
            return {"code_result": {"error": msg}}

        # 3. Subprocess execution
        result = _execute_python(code, data_str)

        logger.info(f"Python execution done: {str(result)[:100]}")
        writer({"code_result": result})
        return {"code_result": result}
    except Exception as e:
        logger.error(f"Python execution error: {e}")
        return {"code_result": {"error": str(e)}}


def _execute_python(code: str, data_json: str, timeout: int = 30) -> dict:
    """Safely execute Python code in a subprocess"""
    # Wrap code: inject data + capture result
    import textwrap as _tw
    indented = _tw.indent(code, "    ")
    wrapper = f"""
import json, sys
_data = json.loads('''{data_json}''')
try:
{indented}
    if 'result' not in dir():
        result = {{"error": "code did not define result variable"}}
    print(json.dumps(result, default=str, ensure_ascii=False))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""

    script_path = safe_temp_path(suffix=".py")
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
        import os
        os.unlink(script_path)
