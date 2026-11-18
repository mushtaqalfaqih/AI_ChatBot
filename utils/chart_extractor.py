"""
chart_extractor.py - Auto Data Visualization Helper

Reads a spreadsheet (CSV/Excel) and returns Chart.js-compatible
JSON payload for the most significant numeric columns.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("app.chart_extractor")

MAX_ROWS_FOR_CHART = 50          # max rows shown in chart (prevents clutter)
MAX_NUMERIC_COLS = 3             # max numeric series per chart
MAX_LABEL_LEN = 20               # truncate long label values


CHART_COLORS = [
    "rgba(99, 102, 241, 0.8)",   # indigo
    "rgba(16, 185, 129, 0.8)",   # emerald
    "rgba(245, 158, 11, 0.8)",   # amber
    "rgba(239, 68, 68, 0.8)",    # red
    "rgba(139, 92, 246, 0.8)",   # violet
]

CHART_BORDER_COLORS = [
    "rgba(99, 102, 241, 1)",
    "rgba(16, 185, 129, 1)",
    "rgba(245, 158, 11, 1)",
    "rgba(239, 68, 68, 1)",
    "rgba(139, 92, 246, 1)",
]


def extract_chart_data(path: Path) -> Optional[Dict[str, Any]]:
    """
    Reads a CSV or Excel file and returns a Chart.js-ready dict, or None if
    the file has no suitable numeric columns.

    Return shape:
    {
        "type": "bar" | "line",
        "title": str,
        "labels": [...],
        "datasets": [
            {"label": str, "data": [...], "backgroundColor": ..., "borderColor": ...}
        ]
    }
    """
    path = Path(path)
    suffix = path.suffix.lower()

    try:
        if suffix == ".csv":
            df = pd.read_csv(
                path,
                encoding="utf-8",
                encoding_errors="ignore",
                nrows=MAX_ROWS_FOR_CHART,
            )
        else:
            engine = "xlrd" if suffix == ".xls" else "openpyxl"
            df = pd.read_excel(path, engine=engine, nrows=MAX_ROWS_FOR_CHART)
    except Exception as e:
        logger.warning(f"chart_extractor: failed to read file: {e}")
        return None

    if df.empty or len(df.columns) < 2:
        return None

    # --- Pick label column (first non-numeric column, or row index) ---
    label_col = None
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            label_col = col
            break

    if label_col is not None:
        labels = [str(v)[:MAX_LABEL_LEN] for v in df[label_col]]
    else:
        labels = [str(i + 1) for i in range(len(df))]

    # --- Pick numeric columns ---
    numeric_cols = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col]) and col != label_col
    ][:MAX_NUMERIC_COLS]

    if not numeric_cols:
        return None

    datasets = []
    for i, col in enumerate(numeric_cols):
        color_i = i % len(CHART_COLORS)
        data_values = df[col].fillna(0).round(2).tolist()
        datasets.append({
            "label": str(col),
            "data": data_values,
            "backgroundColor": CHART_COLORS[color_i],
            "borderColor": CHART_BORDER_COLORS[color_i],
            "borderWidth": 2,
            "borderRadius": 4,
        })

    # Use "line" when many rows (time-series feel), "bar" for fewer
    chart_type = "line" if len(df) > 15 else "bar"

    return {
        "type": chart_type,
        "title": path.stem,
        "labels": labels,
        "datasets": datasets,
    }
