"""YAML pipeline template loader for non-developer pipeline editing."""

import yaml
from pathlib import Path
from typing import Any, Dict, List

from app.workflow.dag import Pipeline
from app.workflow.operators.base import MapOp


class TemplateLoader:
    """Load pipeline definitions from YAML templates."""

    def __init__(self, templates_dir: str):
        self.templates_dir = Path(templates_dir)

    def list_templates(self) -> List[Dict[str, str]]:
        if not self.templates_dir.exists():
            return []
        result = []
        for f in sorted(self.templates_dir.glob("*.yaml")):
            try:
                doc = yaml.safe_load(f.read_text(encoding="utf-8"))
                result.append({"name": doc.get("name", f.stem), "file": f.name, "description": doc.get("description", "")})
            except Exception:
                result.append({"name": f.stem, "file": f.name, "description": "(parse error)"})
        return result

    def load(self, filename: str) -> Pipeline:
        path = self.templates_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Template not found: {filename}")
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return self._build(doc)

    def save(self, name: str, definition: dict) -> str:
        path = self.templates_dir / f"{name}.yaml"
        path.write_text(yaml.dump(definition, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        return str(path)

    def _build(self, doc: dict) -> Pipeline:
        pipe = Pipeline(name=doc["name"], description=doc.get("description", ""))
        nodes: Dict[str, MapOp] = {}

        for step in doc["steps"]:
            node_id = step["id"]
            node = MapOp(name=node_id)
            node._config = step.get("params", {})
            pipe.add_node(node)
            nodes[node_id] = node

        for step in doc["steps"]:
            node_id = step["id"]
            deps = step.get("depends", [])
            for dep in deps:
                if dep in nodes:
                    nodes[dep] >> nodes[node_id]
        return pipe


def from_yaml(templates_dir: str, filename: str) -> Pipeline:
    """Quick load: Pipeline.from_yaml('pipelines/sales.yaml')"""
    loader = TemplateLoader(templates_dir)
    return loader.load(filename)
