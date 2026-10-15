"""Tests for intake index."""
from src.intake.intake_index import build_intake_index


def test_build_intake_index_search():
    documents = [
        {
            "id": "doc1",
            "filename": "spec.pdf",
            "relative_path": "spec.pdf",
            "raw_text": "Pasos: login al portal empresarial con credenciales",
            "extra": {"page_refs": [{"page_number": 1, "text": "Pasos: login"}], "format": "pdf"},
        },
        {
            "id": "doc2",
            "filename": "data.xlsx",
            "relative_path": "data.xlsx",
            "raw_text": "Sheet: Usuarios\nusuario\tclave",
            "extra": {
                "sheet_metadata": [{"name": "Usuarios", "rows": [["usuario", "clave"], ["u1", "secret"]]}],
                "sheet_count": 1,
            },
        },
    ]
    index = build_intake_index("proj-1", documents)
    assert len(index.list_files()) == 2
    hits = index.search_text("login")
    assert hits


def test_list_files_signals():
    documents = [
        {
            "id": "x",
            "filename": "long.pdf",
            "relative_path": "long.pdf",
            "raw_text": "x" * 25000,
            "extra": {"page_refs": [], "sheet_metadata": []},
        }
    ]
    index = build_intake_index("p", documents)
    meta = index.list_files()[0]
    assert meta["filename"] == "long.pdf"
