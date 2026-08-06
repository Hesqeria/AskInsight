"""YAML pipeline template loader with path traversal protection."""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, List

from app.workflow.dag import Pipeline
from app.workflow.operators.base import MapOp


class TemplateLoader:
    """Load pipeline definitions from YAML templates. Safe against path traversal."""

    def __init__(self, templates_dir: str):
        self.templates_dir = Path(templates_dir).resolve()

    @staticmethod
    def _safe_name(filename: str) -> str:
        if not filename or ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError(f"Unsafe template name: {filename}")
        if not filename.endswith(".yaml"):
            filename += ".yaml"
        return filename

    def list_templates(self) -> List[Dict[str, str]]:
        if not self.templates_dir.exists():
            return []
        result = []
        for f in sorted(self.templates_dir.glob("*.yaml")):
            try:
                doc = yaml.safe_load(f.read_text(encoding="utf-8"))
                result.append({
                    "name": doc.get("name", f.stem),
                    "file": f.name,
                    "description": doc.get("description", ""),
                })
            except Exception:
                result.append({"name": f.stem, "file": f.name, "description": "(parse error)"})
        return result

    def load(self, filename: str) -> Pipeline:
        safe_name = self._safe_name(filename)
        path = self.templates_dir / safe_name
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {safe_name}")
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return self._build(doc)

    def save(self, name: str, definition: dict) -> str:
        safe_name = self._safe_name(name)
        path = self.templates_dir / safe_name
        path.write_text(
            yaml.dump(definition, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        return str(path)

    def _build(self, doc: dict) -> Pipeline:
        pipe = Pipeline(name=doc.get("name", ""), description=doc.get("description", ""))
        nodes: Dict[str, MapOp] = {}
        steps = doc.get("steps", [])
        if not steps:
            raise ValueError(f"Template '{doc.get('name')}' has no steps defined")

        for step in steps:
            node_id = step.get("id")
            if not node_id:
                raise ValueError(f"Step missing 'id' in template '{doc.get('name')}'")
            node = MapOp(name=node_id)
            node._config = step.get("params", {})
            pipe.add_node(node)
            nodes[node_id] = node

        for step in steps:
            node_id = step["id"]
            deps = step.get("depends", [])
            for dep in deps:
                if dep in nodes:
                    nodes[dep] >> nodes[node_id]
                else:
                    raise ValueError(f"Dependency '{dep}' not found in template '{doc.get('name')}'")
        return pipe
