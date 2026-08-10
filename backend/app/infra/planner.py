"""P3-05: Task planner - decompose intent into action steps."""
from typing import Any


class TaskPlan:
    def __init__(self, task_id: str, goal: str):
        self.task_id = task_id
        self.goal = goal
        self.steps: list[dict] = []
        self.current_step = 0

    def add_step(self, agent: str, action: str, params: dict, condition: str = None):
        self.steps.append({
            "agent": agent, "action": action, "params": params,
            "condition": condition, "status": "pending",
        })

    def next_step(self) -> dict:
        if self.current_step >= len(self.steps):
            return None
        step = self.steps[self.current_step]
        step["status"] = "running"
        self.current_step += 1
        return step

    def complete_current(self, result: Any):
        if self.current_step > 0:
            self.steps[self.current_step - 1]["status"] = "completed"
            self.steps[self.current_step - 1]["result"] = result


class Planner:
    def plan(self, intent: str, slots: dict) -> TaskPlan:
        plan = TaskPlan(task_id=f"task_{int(time.time())}", goal=f"{intent}: {slots.get('metric', 'unknown')}")

        if intent == "data_query":
            plan.add_step("intent_agent", "recognize", {"text": slots.get("text", "")})
            plan.add_step("sql_agent", "generate", {"metric": slots.get("metric"),
                           "dimensions": slots.get("dimensions", [])})
            plan.add_step("trust_engine", "assess", {})
            plan.add_step("executor", "execute_sql", {})
        elif intent == "anomaly_explain":
            plan.add_step("governance_agent", "detect", {"metric": slots.get("metric")})
            plan.add_step("root_cause", "trace", {})
        elif intent == "etl_generation":
            plan.add_step("etl_agent", "analyze_requirement", {"text": slots.get("requirement", "")})
            plan.add_step("etl_agent", "generate_sql", {})
            plan.add_step("etl_agent", "generate_dag", {})
        else:
            plan.add_step("generic", "handle", {"intent": intent, "slots": slots})

        return plan


import time
_planner = None

def get_planner() -> Planner:
    global _planner
    if _planner is None:
        _planner = Planner()
    return _planner
