import os
"""Intelligent NL2SQL MCP Server.

Lets tools such as opencode / Dify / Claude Desktop invoke NL2SQL capabilities via the MCP protocol.

Usage:
  # Add to opencode.json:
  "data-agent": {
    "type": "local",
    "command": ["python", "-m", "mcp_server.server", "--endpoint", "http://localhost:8000"]
  }

Exposed tools:
  - query(question): natural-language data query, returns results
  - build_knowledge(): trigger a knowledge-base rebuild
  - list_tables(): list available tables
  - get_schema(table_name): get table schema
"""
import argparse
import json
import sys
import httpx


class DataAgentMCP:
    """Intelligent NL2SQL MCP tool set."""

    def __init__(self, endpoint: str = "http://localhost:8000"):
        self.endpoint = endpoint
        self._token = None

    def _login(self) -> str:
        """Log in to obtain a JWT."""
        if self._token:
            return self._token
        r = httpx.post(f"{self.endpoint}/api/login",
                       json={"username": os.getenv("API_USER", "admin"), "password": os.getenv("API_PASSWORD", "admin")}, timeout=10)
        self._token = r.json().get("token", "")
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._login()}"}

    def query(self, question: str) -> str:
        """Natural-language data query.

        Args:
            question: the user's natural-language question (e.g. "ranking of total sales by region")

        Returns:
            Query result (JSON, including data + chart recommendation + decision suggestion)
        """
        r = httpx.post(f"{self.endpoint}/api/query",
                       headers=self._headers(),
                       json={"query": question, "history": []},
                       timeout=300)
        # Parse SSE
        result = {"data": [], "chart": {}, "insights": "", "anomaly": None}
        for line in r.text.split("\n"):
            if line.startswith("data: "):
                try:
                    d = json.loads(line[6:])
                    if isinstance(d.get("result"), list):
                        result["data"] = d["result"]
                    elif d.get("chart_recommendation"):
                        result["chart"] = d["chart_recommendation"]
                    elif d.get("insights"):
                        result["insights"] = d["insights"]
                    elif d.get("anomaly"):
                        result["anomaly"] = d["anomaly"]
                except:
                    pass
        return json.dumps(result, ensure_ascii=False, default=str)

    def list_tables(self) -> str:
        """List available data tables."""
        # Query table_info from Doris
        r = httpx.post(f"{self.endpoint}/api/query",
                       headers=self._headers(),
                       json={"query": "list all available tables", "history": []},
                       timeout=60)
        # Simplification: directly return the known table list
        tables = [
            "fact_order (order fact table)",
            "dim_region (region dimension table)",
            "dim_customer (customer dimension table)",
            "dim_product (product dimension table)",
            "dim_date (date dimension table)",
            "dws_sales_wide (sales wide table)",
            "dws_region_summary (region summary table)",
            "dws_category_summary (category summary table)",
            "ads_customer_profile (customer profile tag table)",
        ]
        return "\n".join(tables)

    def get_schema(self, table_name: str) -> str:
        """Get structural information for a specified table.

        Args:
            table_name: table name (e.g. fact_order)
        """
        schemas = {
            "fact_order": "order_id(PK), customer_id(FK), product_id(FK), date_id(FK), region_id(FK), order_quantity(measure), order_amount(measure)",
            "dim_region": "region_id(PK), province, region_name, country",
            "dim_customer": "customer_id(PK), customer_name, gender, member_level",
            "dim_product": "product_id(PK), product_name, category, brand",
            "dim_date": "date_id(PK), year, quarter, month, day",
            "ads_customer_profile": "customer_id(PK), total_amount, rfm_segment, is_high_value, preferred_category",
        }
        return schemas.get(table_name, f"Table {table_name} not found, available tables: {list(schemas.keys())}")


def main():
    parser = argparse.ArgumentParser(description="Intelligent NL2SQL MCP Server")
    parser.add_argument("--endpoint", default="http://localhost:8000", help="API endpoint")
    args = parser.parse_args()

    agent = DataAgentMCP(args.endpoint)

    # Simple stdio interaction mode (simplified MCP protocol)
    print("Intelligent NL2SQL MCP Server started", file=sys.stderr)
    print(f"Endpoint: {args.endpoint}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            tool = msg.get("tool", "")
            params = msg.get("params", {})

            if tool == "query":
                result = agent.query(params.get("question", ""))
            elif tool == "list_tables":
                result = agent.list_tables()
            elif tool == "get_schema":
                result = agent.get_schema(params.get("table_name", ""))
            else:
                result = json.dumps({"error": f"Unknown tool: {tool}"})

            print(json.dumps({"result": result}, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
