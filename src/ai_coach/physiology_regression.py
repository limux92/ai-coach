"""OLS work-time regression; Codex corrected/reviewed GPT-oss draft mathematics."""
import math


def regress(efforts):
    if len(efforts) < 2:
        raise ValueError("At least two efforts required")
    xs, outputs = [], []
    for effort in efforts:
        x, p = effort.get("duration_seconds"), effort.get("mean_output")
        if (type(x) not in (int, float) or type(p) not in (int, float)
                or not 120 < x < 900 or not 0 < p < 1e9 or not math.isfinite(p)):
            raise ValueError("Invalid duration or output")
        xs.append(float(x))
        outputs.append(float(p))
    n = len(xs)
    ys = [x * p for x, p in zip(xs, outputs)]
    xm, ym = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xm) ** 2 for x in xs)
    if sxx <= 1e-12:
        raise ValueError("Duration spread required")
    cp = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / sxx
    capacity = ym - cp * xm
    if cp <= 0 or capacity <= 0:
        raise ValueError("Nonphysical fit")
    squared_error = sum((y - cp * x - capacity) ** 2 for x, y in zip(xs, ys))
    variance = squared_error / (n - 2) if n > 2 else None
    return {"critical_output": cp, "capacity": capacity,
            "rmse_output": math.sqrt(sum((p - cp - capacity / x) ** 2 for x, p in zip(xs, outputs)) / n),
            "critical_output_standard_error": math.sqrt(variance / sxx) if variance is not None else None,
            "capacity_standard_error": math.sqrt(variance * (1 / n + xm * xm / sxx)) if variance is not None else None}
