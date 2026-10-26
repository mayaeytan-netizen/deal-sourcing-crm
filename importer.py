"""Bulk-import companies from an uploaded .xlsx/.xls/.csv file, and the
reverse: export the current pipeline back out to .xlsx.

Column headers are matched flexibly (case-insensitive, a few common
aliases) -- only a company name column is required. Everything else is
optional and falls back to sensible defaults, same as adding one company
by hand through the form.
"""

import csv
import io

import openpyxl
from openpyxl.styles import Font

from cadence import STAGES

COLUMN_ALIASES = {
    "name": {"name", "company", "company name", "companies", "account", "account name"},
    "contact_name": {"contact", "contact name", "contact person", "poc"},
    "contact_email": {"email", "contact email", "e-mail"},
    "website": {"website", "url", "site", "web"},
    "notes": {"notes", "note", "description", "context"},
    "stage": {"stage", "pipeline stage", "status"},
}

STAGE_LOOKUP = {s.lower(): s for s in STAGES}


class ImportError_(Exception):
    pass


def _normalize_header(value) -> str:
    return str(value or "").strip().lower()


def _map_headers(headers) -> dict:
    """Returns {field_name: column_index} for whichever columns were found."""
    mapping = {}
    for idx, raw in enumerate(headers):
        header = _normalize_header(raw)
        if not header:
            continue
        for field, aliases in COLUMN_ALIASES.items():
            if header in aliases and field not in mapping:
                mapping[field] = idx
    return mapping


def _rows_from_xlsx(file_bytes: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    sheet = wb.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    try:
        headers = next(rows)
    except StopIteration:
        return [], []
    return list(headers), [list(r) for r in rows]


def _rows_from_csv(file_bytes: bytes):
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def parse_companies_file(filename: str, file_bytes: bytes) -> dict:
    """Returns {"companies": [...], "skipped": int, "total_rows": int}.
    Each company dict has keys: name, contact_name, contact_email, website,
    notes, stage (all but name may be None). Raises ImportError_ with a
    human-readable message for an unsupported file type or unreadable file."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()

    try:
        if ext in ("xlsx", "xlsm"):
            headers, data_rows = _rows_from_xlsx(file_bytes)
        elif ext == "csv":
            headers, data_rows = _rows_from_csv(file_bytes)
        elif ext == "xls":
            raise ImportError_(
                "Old-style .xls files aren't supported -- save it as .xlsx or .csv and try again."
            )
        else:
            raise ImportError_("Unsupported file type -- upload a .xlsx or .csv file.")
    except ImportError_:
        raise
    except Exception as exc:
        raise ImportError_(f"Couldn't read that file ({exc}).")

    mapping = _map_headers(headers)
    if "name" not in mapping:
        raise ImportError_(
            "Couldn't find a company name column -- the first row should have a header "
            "like \"Company\" or \"Company Name\"."
        )

    def cell(row, field):
        idx = mapping.get(field)
        if idx is None or idx >= len(row):
            return None
        value = row[idx]
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    companies = []
    skipped = 0
    for row in data_rows:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        name = cell(row, "name")
        if not name:
            skipped += 1
            continue
        stage_raw = cell(row, "stage")
        stage = STAGE_LOOKUP.get((stage_raw or "").strip().lower(), "Not Contacted")
        companies.append(
            {
                "name": name,
                "contact_name": cell(row, "contact_name"),
                "contact_email": cell(row, "contact_email"),
                "website": cell(row, "website"),
                "notes": cell(row, "notes"),
                "stage": stage,
            }
        )

    return {"companies": companies, "skipped": skipped, "total_rows": len(data_rows)}


EXPORT_HEADERS = [
    "Company Name",
    "Contact Name",
    "Contact Email",
    "Website",
    "Notes",
    "Stage",
    "Date Added",
    "Last Touch Date",
    "Next Touch Date",
    "Total Touches",
]


def export_companies_to_xlsx(companies) -> bytes:
    """`companies`: an iterable of sqlite3.Row (or dict-like) company
    records. Returns the raw bytes of an .xlsx file -- same column layout
    the importer reads, plus a few read-only cadence columns, so a round
    trip (export, edit, re-import) works without reshaping anything."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Companies"

    ws.append(EXPORT_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for c in companies:
        ws.append(
            [
                c["name"],
                c["contact_name"],
                c["contact_email"],
                c["website"],
                c["notes"],
                c["stage"],
                c["date_added"],
                c["last_touch_date"],
                c["next_touch_date"],
                c["total_touches"],
            ]
        )

    widths = [28, 16, 24, 24, 50, 14, 12, 14, 14, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
