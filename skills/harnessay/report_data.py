"""Portable report exports and comparisons; no session parsing or file access."""
import datetime
import math


SCHEMA_VERSION = "2026-09-evidence-v1"
USAGE_FIELDS = ("input_tokens", "uncached_input_tokens", "fresh_input_tokens", "cached_input_tokens",
                "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")


def usage_value(usage, metric):
    """Only complete observed coverage is comparable; absent and partial remain unknown."""
    field = usage.get("fields", {}).get(metric, {})
    if usage.get("records", 0) > 0 and field.get("records") == usage["records"]:
        return field["tokens"]
    return None


def export_stats(st):
    """Convert aggregate Counters and tuple keys into ordinary JSON containers."""
    projects = [{"project": str(project), **dict(counters)}
                for project, counters in st.get("projects", {}).items()]
    totals = dict(st.get("totals", {}))
    totals.setdefault("sessions", sum(row.get("sessions", 0) for row in projects))
    result = {
        "schema_version": SCHEMA_VERSION,
        "source": st.get("source", "claude"),
        "since": st.get("since"), "until": st.get("until"),
        "totals": totals, "projects": projects,
        "tools": [{"tool": str(tool), **dict(counters)}
                  for tool, counters in st.get("tools", {}).items()],
        "reads": [{"project": str(project), "file": str(path), "calls": calls}
                  for (project, path), calls in st.get("reads", {}).items()],
        "redundant_reads": [{"project": str(project), "file": str(path), "bytes": size}
                            for (project, path), size in st.get("redundant", {}).items()],
        "diagnostics": dict(st.get("diagnostics", {})),
        "evidence": st.get("evidence", {}),
    }
    if "usage" in st:
        result["usage"] = st["usage"]
    return result


def _number(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value < 0 or (isinstance(value, float) and not math.isfinite(value))):
        raise ValueError("report metrics must be finite nonnegative numbers")


def _validate(report):
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported report schema_version")
    if report.get("source") not in ("claude", "codex", "all"):
        raise ValueError("report source must be claude, codex, or all")
    if "usage" in report:
        usage = report["usage"]
        if (not isinstance(usage, dict) or type(usage.get("records")) is not int
                or usage["records"] < 0 or not isinstance(usage.get("fields"), dict)
                or set(usage["fields"]) != set(USAGE_FIELDS)):
            raise ValueError("invalid usage coverage")
        for field in usage["fields"].values():
            if (not isinstance(field, dict) or type(field.get("tokens")) is not int
                    or field["tokens"] < 0 or type(field.get("records")) is not int
                    or not 0 <= field["records"] <= usage["records"]
                    or (field["records"] == 0 and field["tokens"] != 0)):
                raise ValueError("invalid usage field coverage")
    for field in ("totals", "diagnostics", "evidence"):
        if not isinstance(report.get(field), dict):
            raise ValueError("report %s must be an object" % field)
    for field in ("totals", "diagnostics"):
        for key, value in report[field].items():
            if not isinstance(key, str):
                raise ValueError("counter names must be strings")
            _number(value)
    for field, labels, required in (
            ("projects", ("project",), ()), ("tools", ("tool",), ()),
            ("reads", ("project", "file"), ("calls",)),
            ("redundant_reads", ("project", "file"), ("bytes",))):
        if not isinstance(report.get(field), list):
            raise ValueError("report %s must be a list" % field)
        for row in report[field]:
            if not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in labels):
                raise ValueError("invalid %s row" % field)
            if any(key not in row for key in required):
                raise ValueError("missing %s metric" % field)
            for key, value in row.items():
                if not isinstance(key, str):
                    raise ValueError("counter names must be strings")
                if key not in labels:
                    _number(value)
    dates = {}
    for key in ("since", "until"):
        value = report.get(key)
        if value is None:
            dates[key] = None
            continue
        try:
            date = datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError("%s must be YYYY-MM-DD or null" % key)
        if date.isoformat() != value:
            raise ValueError("%s must be YYYY-MM-DD or null" % key)
        dates[key] = date
    if dates["since"] and dates["until"] and dates["until"] <= dates["since"]:
        raise ValueError("until must be later than since (exclusive end)")
    return dates


def _metrics(report):
    totals = report["totals"]
    sessions = totals.get("sessions", sum(row.get("sessions", 0) for row in report["projects"]))
    result_bytes, compactions = totals.get("result_bytes", 0), totals.get("compactions", 0)
    read_bytes = sum(row.get("bytes", 0) for row in report["tools"] if row["tool"] == "Read")
    metrics = {
        "sessions": sessions, "result_bytes": result_bytes,
        "output_tokens": totals.get("output", 0) + totals.get("sidechain_output", 0),
        "compactions": compactions,
        "bytes_per_session": result_bytes / sessions if sessions else None,
        "compactions_per_session": compactions / sessions if sessions else None,
        "redundant_read_percent": totals.get("redundant_bytes", 0) * 100 / read_bytes if read_bytes else None,
    }
    for metric in USAGE_FIELDS:
        if metric != "output_tokens" or "usage" in report:
            metrics[metric] = usage_value(report.get("usage", {}), metric)
    return metrics


def compare_reports(current, baseline):
    """Compare compatible exports; undefined ratios remain null, never infinity."""
    current_dates, baseline_dates = _validate(current), _validate(baseline)
    if current["source"] != baseline["source"]:
        raise ValueError("reports must use the same source")
    before, after = _metrics(baseline), _metrics(current)
    warnings = []
    if any(value is None for dates in (current_dates, baseline_dates) for value in dates.values()):
        warnings.append("Missing date bounds; period lengths cannot be compared.")
    elif current_dates["until"] - current_dates["since"] != baseline_dates["until"] - baseline_dates["since"]:
        warnings.append("Period lengths differ; absolute changes may reflect different coverage.")
    for name, metrics in (("Baseline", before), ("Current", after)):
        if not metrics["sessions"]:
            warnings.append("%s has zero sessions; per-session metrics are unavailable." % name)
        if "usage" in current or "usage" in baseline:
            missing = [key for key in USAGE_FIELDS if metrics[key] is None]
            if missing:
                warnings.append("%s has missing or partial token coverage: %s. Changes are unavailable."
                                % (name, ", ".join(missing)))
    rows = []
    for metric, previous in before.items():
        value = after[metric]
        delta = None if previous is None or value is None else value - previous
        percent = None
        if delta is not None:
            percent = delta / previous * 100 if previous else (0 if value == 0 else None)
        rows.append({"metric": metric, "before": previous, "after": value,
                     "delta": delta, "percent_change": percent})
    return {
        "baseline": {key: baseline.get(key) for key in ("since", "until", "source")},
        "current": {key: current.get(key) for key in ("since", "until", "source")},
        "metrics": rows, "warnings": warnings,
    }
