"""Statistical analysis service: built-in common statistical calculations (no LLM needed).

Supports:
  - Descriptive statistics (mean/median/std/quantiles)
  - Period-over-period and year-over-year calculations
  - Share/proportion calculations
  - Correlation analysis
  - Ranking + gap
"""
import math


def describe(values: list[float]) -> dict:
    """Descriptive statistics."""
    if not values:
        return {"count": 0}
    n = len(values)
    avg = sum(values) / n
    std = math.sqrt(sum((v - avg) ** 2 for v in values) / n) if n > 1 else 0
    sorted_v = sorted(values)
    median = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    return {
        "count": n,
        "sum": round(sum(values), 2),
        "mean": round(avg, 2),
        "median": round(median, 2),
        "std": round(std, 2),
        "min": min(values),
        "max": max(values),
        "q25": round(sorted_v[n // 4], 2) if n >= 4 else None,
        "q75": round(sorted_v[3 * n // 4], 2) if n >= 4 else None,
    }


def growth_rate(current: float, previous: float) -> dict:
    """Period-over-period growth rate."""
    if previous == 0:
        return {"growth_rate": None, "direction": "N/A"}
    rate = (current - previous) / previous * 100
    return {
        "current": current,
        "previous": previous,
        "growth_rate_pct": round(rate, 2),
        "direction": "up" if rate > 0 else "down" if rate < 0 else "flat",
    }


def percentage_breakdown(data: list[dict], category_col: str, value_col: str) -> list[dict]:
    """Share calculation."""
    total = sum(row.get(value_col, 0) for row in data)
    if total == 0:
        return data
    result = []
    for row in data:
        new_row = dict(row)
        val = row.get(value_col, 0)
        new_row["percentage"] = round(val / total * 100, 2)
        result.append(new_row)
    result.sort(key=lambda x: x.get(value_col, 0), reverse=True)
    return result


def rank_with_gap(data: list[dict], value_col: str, top_n: int = 10) -> list[dict]:
    """Ranking + gap to the leader."""
    sorted_data = sorted(data, key=lambda x: x.get(value_col, 0), reverse=True)[:top_n]
    if not sorted_data:
        return []
    top_value = sorted_data[0].get(value_col, 0)
    result = []
    for i, row in enumerate(sorted_data):
        new_row = dict(row)
        new_row["rank"] = i + 1
        new_row["gap_to_top"] = round(top_value - row.get(value_col, 0), 2)
        new_row["gap_pct"] = round((1 - row.get(value_col, 0) / top_value) * 100, 2) if top_value > 0 else 0
        result.append(new_row)
    return result


def correlation(data: list[dict], col_x: str, col_y: str) -> dict:
    """Pearson correlation coefficient."""
    xs = [row.get(col_x, 0) for row in data]
    ys = [row.get(col_y, 0) for row in data]
    n = len(xs)
    if n < 2:
        return {"correlation": None}
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return {"correlation": None}
    r = num / (den_x * den_y)
    strength = "strong" if abs(r) > 0.7 else "moderate" if abs(r) > 0.4 else "weak"
    direction = "positive" if r > 0 else "negative"
    return {
        "correlation": round(r, 4),
        "strength": strength,
        "direction": direction,
        "interpretation": f"{strength} {direction} correlation (r={r:.4f})",
    }


def cross_analysis(data: list[dict], row_col: str, col_col: str, value_col: str, agg: str = "sum") -> dict:
    """Cross analysis (row x column matrix)."""
    matrix = {}
    row_labels = set()
    col_labels = set()
    for row in data:
        r_val = str(row.get(row_col, ""))
        c_val = str(row.get(col_col, ""))
        v = row.get(value_col, 0)
        row_labels.add(r_val)
        col_labels.add(c_val)
        key = (r_val, c_val)
        if key not in matrix:
            matrix[key] = []
        matrix[key].append(v)

    # Aggregate
    result_matrix = {}
    for key, values in matrix.items():
        if agg == "sum":
            result_matrix[key] = sum(values)
        elif agg == "avg":
            result_matrix[key] = round(sum(values) / len(values), 2)
        elif agg == "count":
            result_matrix[key] = len(values)
        elif agg == "max":
            result_matrix[key] = max(values)
        else:
            result_matrix[key] = sum(values)

    return {
        "row_labels": sorted(row_labels),
        "col_labels": sorted(col_labels),
        "matrix": {f"{k[0]}|{k[1]}": v for k, v in result_matrix.items()},
    }
