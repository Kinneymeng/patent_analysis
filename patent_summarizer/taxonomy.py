from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TaxonomyItem:
    first_level: str
    second_level: str


@dataclass(frozen=True)
class Taxonomy:
    items: list[TaxonomyItem]

    def contains(self, first_level: str, second_level: str) -> bool:
        return any(
            item.first_level == first_level and item.second_level == second_level
            for item in self.items
        )


def load_taxonomy(path: Path) -> Taxonomy:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl>=3.1 is required to read class workbooks") from exc

    wb = load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    merged_first_levels = _merged_first_level_values(ws)

    items: list[TaxonomyItem] = []
    current_first = ""
    for row in range(2, ws.max_row + 1):
        raw_first = ws.cell(row=row, column=1).value
        raw_second = ws.cell(row=row, column=2).value
        first = _clean(raw_first) or merged_first_levels.get(row) or current_first
        second = _clean(raw_second)
        if first:
            current_first = first
        if first and second:
            items.append(TaxonomyItem(first, second))
    return Taxonomy(items)


def _merged_first_level_values(ws) -> dict[int, str]:
    values: dict[int, str] = {}
    for merged in ws.merged_cells.ranges:
        if merged.min_col != 1 or merged.max_col != 1:
            continue
        value = _clean(ws.cell(row=merged.min_row, column=1).value)
        if not value:
            continue
        for row in range(merged.min_row, merged.max_row + 1):
            values[row] = value
    return values


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
