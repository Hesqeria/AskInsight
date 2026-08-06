from pathlib import Path


def load_prompt(name: str) -> str:
    p = Path(__file__).parents[2] / "prompts" / f"{name}.prompt"
    return p.read_text(encoding="utf-8")
