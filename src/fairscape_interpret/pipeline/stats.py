"""Dataset statistics formatting for LLM prompts.

Converts `descriptiveStatistics` and `splitStatistics` dicts into prompt-ready
markdown tables with mini histograms. Storage-agnostic; the adapter is
responsible for fetching the stats dicts for a given dataset @id.
"""

from __future__ import annotations

from typing import Any, Dict

# Maximum number of splits to include per dataset in the prompt.
MAX_SPLITS = 10

_HIST_BARS = " ▁▂▃▄▅▆▇█"


def _mini_histogram(counts: list) -> str:
    """Render a list of histogram counts as a sparkline string."""
    if not counts:
        return ""
    mx = max(counts) if max(counts) > 0 else 1
    return "".join(_HIST_BARS[min(int(c / mx * 8) + (1 if c > 0 else 0), 8)] for c in counts)


def _format_column_stats(col_name: str, col_data: dict) -> str:
    """Format one column's stats as a markdown table row."""
    stats = col_data.get("statistics", {})

    # Determine type by presence of 'mean' (numerical) vs 'unique' (categorical)
    if "mean" in stats and stats["mean"] is not None:
        # Numerical
        missing_pct = stats.get("missing_percentage", "")
        if missing_pct != "" and missing_pct is not None:
            missing_pct = f"{missing_pct}%"
        hist = _mini_histogram(stats.get("histogram_counts", []))
        return (
            f"| {col_name} | num | {stats.get('count', '')} | {missing_pct} "
            f"| {stats.get('mean', '')} | {stats.get('std', '')} "
            f"| {stats.get('min', '')} | {stats.get('first_quartile', '')} "
            f"| {stats.get('second_quartile', '')} | {stats.get('third_quartile', '')} "
            f"| {stats.get('max', '')} | {hist} |"
        )
    else:
        # Categorical
        missing_pct = stats.get("missing_percentage", "")
        if missing_pct != "" and missing_pct is not None:
            missing_pct = f"{missing_pct}%"
        return (
            f"| {col_name} | cat | {stats.get('count', '')} | {missing_pct} "
            f"| top: {stats.get('top', '')} | uniq: {stats.get('unique', '')} "
            f"| | | | | | freq: {stats.get('freq', '')} |"
        )


def _format_dataset_stats(ds_name: str, ds_stats: dict) -> str:
    """Format descriptiveStatistics and splitStatistics for one dataset into
    prompt-ready markdown. Returns empty string if no stats available."""
    desc_stats = ds_stats.get("descriptiveStatistics", {})
    split_stats = ds_stats.get("splitStatistics", {})

    if not desc_stats and not split_stats:
        return ""

    parts = []

    # --- Overall descriptive statistics ---
    if desc_stats:
        columns = list(desc_stats.items())
        total_cols = len(columns)

        # Estimate row count from first column's count
        first_stats = columns[0][1].get("statistics", {}) if columns else {}
        row_count = first_stats.get("count", "?")

        # Total missing across all columns
        total_missing = 0
        for _, col_data in columns:
            mc = col_data.get("statistics", {}).get("missing_count")
            if mc is not None:
                total_missing += mc

        # Skip detailed stats for wide datasets (>10 columns) to keep prompts small
        if total_cols > 10:
            parts.append(f"#### Data Profile: {ds_name} ({total_cols} columns, ~{row_count} rows, {total_missing} missing values)")
            parts.append("*(Column-level statistics omitted for wide dataset)*")
            parts.append("")
        else:
            display_cols = columns

            parts.append(f"#### Data Profile: {ds_name} ({total_cols} columns, ~{row_count} rows, {total_missing} missing values)")
            parts.append("| Column | Type | Count | Missing% | Mean/Top | Std/Unique | Min | Q1 | Median | Q3 | Max | Hist |")
            parts.append("|--------|------|-------|----------|----------|------------|-----|----|----|----|----|------|")
            for col_name, col_data in display_cols:
                parts.append(_format_column_stats(col_name, col_data))
            parts.append("")

    # --- Split statistics ---
    if split_stats:
        split_items = list(split_stats.items())
        total_splits = len(split_items)
        truncated_splits = total_splits > MAX_SPLITS
        display_splits = split_items[:MAX_SPLITS]

        for split_name, split_data in display_splits:
            split_desc = split_data.get("description", "")
            split_col_stats = split_data.get("statistics", {})
            if not split_col_stats:
                continue

            # Row count from first column
            first_split_col = list(split_col_stats.values())[0] if split_col_stats else {}
            split_row_count = first_split_col.get("statistics", {}).get("count", "?")

            label = f'#### Split: "{split_name}"'
            if split_desc:
                label += f" ({split_desc})"
            label += f" -- {split_row_count} rows"
            parts.append(label)

            split_columns = list(split_col_stats.items())

            # Skip detailed split stats for wide datasets
            if len(split_columns) > 10:
                parts.append(f"*({len(split_columns)} columns, stats omitted for wide dataset)*")
                parts.append("")
            else:
                parts.append("| Column | Type | Count | Missing% | Mean/Top | Std/Unique | Min | Q1 | Median | Q3 | Max | Hist |")
                parts.append("|--------|------|-------|----------|----------|------------|-----|----|----|----|----|------|")
                for col_name, col_data in split_columns:
                    parts.append(_format_column_stats(col_name, col_data))
                parts.append("")

        if truncated_splits:
            parts.append(f"*... {total_splits - MAX_SPLITS} more splits omitted*\n")

    return "\n".join(parts)
