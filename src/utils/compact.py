def compact_collected(collected_data: dict) -> str:
    """Build a compact one-line-per-entry view of collected_data.

    Format per line:
        key | label: value unit (period)           [no notes]
        key | label: value unit (period) [notes]   [has notes]

    The key is the exact identifier used in DATA_REFS; label gives the
    human-readable Chinese name; notes surfaces any caveats.
    """
    lines = []
    for k, v in collected_data.items():
        if not isinstance(v, dict):
            continue
        label = v.get("label", "")
        val = v.get("value", "")
        unit = v.get("unit", "")
        period = v.get("period", "")
        notes = v.get("notes", "")
        head = (
            f"{k} | {label}: {val} {unit} ({period})"
            if label
            else f"{k}: {val} {unit} ({period})"
        )
        lines.append(f"{head} [{notes}]" if notes else head)
    return "\n".join(lines)
