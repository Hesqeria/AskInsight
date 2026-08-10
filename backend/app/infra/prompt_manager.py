"""P3-02: Prompt management center - template store, render."""
from datetime import datetime


class PromptManager:
    DEFAULTS = {
        ("intent_agent", "main"): "Intent from query: {query}. Metrics: {metrics}. Glossary: {glossary}.",
        ("sql_agent", "main"): "Generate Doris SQL. Metric: {metric}. Dimensions: {dimensions}. Time: {time_range}.",
        ("etl_agent", "main"): "Analyze ETL requirement: {requirement}. Source: {sources}.",
    }

    def render(self, agent: str, name: str, variables: dict) -> str:
        template = self.DEFAULTS.get((agent, name), "{query}")
        try:
            return template.format(**variables)
        except KeyError:
            return str(variables)

    def get_template(self, agent: str, name: str) -> str:
        return self.DEFAULTS.get((agent, name), "{query}")
