"""Generic PDF extraction — pages, text, tables."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def extract_pdf(path: Path) -> Dict[str, Any]:
    pages: List[Dict[str, Any]] = []
    all_tables: List[Dict[str, Any]] = []

    try:
        import fitz

        doc = fitz.open(path)
        for idx, page in enumerate(doc):
            text = page.get_text() or ""
            page_tables: List[List[List[str]]] = []
            try:
                for table in page.find_tables().tables:
                    rows = [[str(c or "") for c in row] for row in table.extract()]
                    if rows:
                        page_tables.append(rows)
                        all_tables.append(
                            {"page": idx + 1, "rows": rows, "row_count": len(rows)}
                        )
            except Exception:
                pass
            pages.append(
                {
                    "page_number": idx + 1,
                    "text": text,
                    "char_count": len(text),
                    "tables": page_tables,
                }
            )
        doc.close()
    except Exception:
        try:
            import pdfplumber

            with pdfplumber.open(path) as pdf:
                for idx, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    page_tables: List[List[List[str]]] = []
                    for table in page.extract_tables() or []:
                        rows = [[str(c or "") for c in row] for row in table]
                        if rows:
                            page_tables.append(rows)
                            all_tables.append(
                                {"page": idx + 1, "rows": rows, "row_count": len(rows)}
                            )
                    pages.append(
                        {
                            "page_number": idx + 1,
                            "text": text,
                            "char_count": len(text),
                            "tables": page_tables,
                        }
                    )
        except Exception:
            raw = path.read_bytes().decode("utf-8", errors="replace")
            pages = [{"page_number": 1, "text": raw, "char_count": len(raw), "tables": []}]

    full_text = "\n".join(p["text"] for p in pages)
    return {
        "format": "pdf",
        "page_count": len(pages),
        "pages": pages,
        "tables": all_tables,
        "text": full_text,
        "metadata": {"source": str(path.name)},
    }
