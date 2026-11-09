"""Auditable Excel/CSV exports shared by both ranking modes."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .pipeline import require_rank
from .storage import file_hash, now, read_csv, text, write_csv, write_json


def _sheet(wb, title, columns, rows, links=False):
    ws = wb.create_sheet(title)
    ws.freeze_panes = "C2"
    ws.sheet_view.showGridLines = False
    header_font, body_font = Font(bold=True, color="FFFFFF"), Font(size=11, color="25354A")
    fill, align = PatternFill("solid", fgColor="173B57"), Alignment(wrap_text=True, vertical="top")
    for i, (key, _) in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(i)].width = 65 if key in {"description", "followed_by_list", "value"} else 25
    headers = []
    for _, label in columns:
        cell = WriteOnlyCell(ws, label)
        cell.font, cell.fill, cell.alignment = header_font, fill, align
        headers.append(cell)
    ws.append(headers)
    count = 0
    for count, row in enumerate(rows, 1):
        if count > 1048575:
            raise ValueError("Excel row limit reached; use the full rankings/*.csv data instead")
        cells = []
        for key, _ in columns:
            value = text(row.get(key, ""))
            if isinstance(value, str):
                value = value[:32767]
            cell = WriteOnlyCell(ws, value)
            if isinstance(value, str):
                cell.data_type = "s"  # Never interpret profile text as a formula.
            cell.font, cell.alignment = body_font, align
            if links and key == "profile_url" and value:
                cell.hyperlink = value
                cell.font = Font(color="0563C1", underline="single")
            cells.append(cell)
        ws.append(cells)
    ws.auto_filter.ref = "A1:{}{}".format(get_column_letter(len(columns)), count + 1)
    return count


def report(workspace, mode, top=100):
    if top < 1:
        raise ValueError("top must be positive")
    rank_meta = require_rank(workspace, mode)
    aggregate_meta = workspace.require_aggregate()
    all_rows = read_csv(workspace.file("rankings/" + mode + "/all.csv"))
    selected = read_csv(workspace.file("rankings/" + mode + "/selected.csv"))
    first = selected[:top]
    for row in all_rows + selected:
        row["rank"], row["common_follows"] = int(row["rank"]), int(row["common_follows"])
        row["profile_url"] = "https://x.com/" + row["userName"]
        if mode == "gems":
            row["gem_index"] = float(row["gem_index"])
            row["followers_count"] = int(row["followers_count"])
    seeds = {s["userName"].lower(): s for s in workspace.seeds()}
    edges = []
    for row in first:
        sources = row["followed_by_list"].split("|")
        if len(set(s.lower() for s in sources)) != row["common_follows"]:
            raise ValueError("Co-follow source count mismatch")
        for source in sources:
            seed = seeds[source.lower()]
            edges.append({"target": row["userName"], "common_follows": row["common_follows"],
                          "source": seed["userName"], "name": seed.get("name", "")})
    coverage = aggregate_meta["coverage"]
    methods = [
        {"key": "Voting population", "value": "input/seeds.csv from the supplied list; actual personal followings are exclusions only."},
        {"key": "Original members / voting sources", "value": "{} / {}".format(len(workspace.members()), len(seeds))},
        {"key": "Exclusions", "value": "All original members, optional already-followed snapshot, and optional owner."},
        {"key": "Ranking", "value": "Distinct source count descending, username ties; no follower statistics." if mode == "cofollow" else "100 * common_follows / sqrt(followers + smoothing), descending; username ties."},
        {"key": "Effective settings", "value": str(rank_meta["settings"])},
        {"key": "Gem smoothing", "value": workspace.config.gem_smoothing if mode == "gems" else "Not used"},
        {"key": "Coverage", "value": "{} requested sources; {} available. Unavailable sources contribute unknown, not verified zero.".format(len(coverage), aggregate_meta["available_sources"])},
        {"key": "Snapshot records", "value": "{} to {}; imported cache timestamps are file modification times, not live relationship verification.".format(min(r["fetched_at"] for r in coverage), max(r["fetched_at"] for r in coverage))},
        {"key": "Interpretation", "value": "Network-overlap and popularity heuristics; not a content-quality endorsement."},
        {"key": "Exported at", "value": now()},
    ]
    columns = [("rank", "Rank"), ("userName", "Account"), ("name", "Name"), ("common_follows", "Shared follows")]
    if mode == "gems":
        columns += [("followers_count", "Total followers (snapshot)"), ("gem_index", "Gem score")]
    columns += [("account_type", "Profile type"), ("description", "Bio"), ("followed_by_list", "All voting sources"),
                ("profile_url", "X profile"), ("selection_reason", "Selection reason")]
    wb = Workbook(write_only=True)
    counts = {
        "Top accounts": _sheet(wb, "Top accounts", columns, first, links=True),
        "All candidates": _sheet(wb, "All candidates", columns, all_rows),
        "Shared-follow evidence": _sheet(wb, "Shared-follow evidence", [("target", "Target"), ("common_follows", "Shared follows"), ("source", "Voting source"), ("name", "Source name")], edges),
        "Source coverage": _sheet(wb, "Source coverage", [("userName", "Source"), ("status", "Status"), ("following_count", "Retrieved relationships"), ("fetched_at", "Recorded at"), ("time_basis", "Time basis"), ("reason", "Reason")], coverage),
        "Method": _sheet(wb, "Method", [("key", "Item"), ("value", "Explanation")], methods),
    }
    output = workspace.file("output/" + mode + ".xlsx")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output.parent, suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(temporary)
        with zipfile.ZipFile(temporary) as archive:
            if any("externalLinks/" in name for name in archive.namelist()):
                raise ValueError("Unexpected external workbook links")
        check = load_workbook(temporary, read_only=True, data_only=False)
        try:
            if check._external_links:
                raise ValueError("Unexpected external workbook links")
            for ws in check:
                n = 0
                for n, row in enumerate(ws.iter_rows(), 1):
                    if any(cell.data_type == "f" for cell in row):
                        raise ValueError("Unexpected formula in exported text")
                if n - 1 != counts[ws.title]:
                    raise ValueError("Workbook row count mismatch: " + ws.title)
        finally:
            check.close()
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    csv_path = workspace.file("output/" + mode + ".csv")
    write_csv(csv_path, selected, [key for key, _ in columns], safe=True)
    result = {"created_at": now(), "workbook": str(output), "csv": str(csv_path), "selected_accounts": len(selected),
              "sheets": counts, "rank_signature": rank_meta["signature"], "workbook_hash": file_hash(output),
              "external_workbook_links": 0}
    write_json(workspace.file("output/" + mode + ".validation.json"), result)
    return result
