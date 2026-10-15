from pathlib import Path

from src.intake.parser import parse_text_file, detect_doc_type
from src.intake.normalizer import normalize
from src.intake.schemas.canonical import DocumentTypeEnum

FIXTURES = Path(__file__).parent / "fixtures" / "documents"


def test_detect_brd():
    assert detect_doc_type("BRD_sample.txt", "Business Requirements") == DocumentTypeEnum.BRD


def test_parse_fixture_files():
    for name, expected in [
        ("BRD_sample.txt", DocumentTypeEnum.BRD),
        ("PRD_sample.txt", DocumentTypeEnum.PRD),
        ("UC_sample.txt", DocumentTypeEnum.UC),
        ("AC_sample.txt", DocumentTypeEnum.AC),
    ]:
        path = FIXTURES / name
        doc = normalize(parse_text_file(path, "proj-1", f"doc-{name}"))
        assert doc.document_type == expected
        assert len(doc.sections) >= 1
        assert doc.raw_text


def test_parse_bytes_plain():
    from src.intake.parser import parse_bytes

    doc = parse_bytes(b"Acceptance Criteria for login", "ac.txt", "p1", "d1")
    assert doc.document_type == DocumentTypeEnum.AC


def test_parse_svg_and_png_bytes():
    from src.intake.parser import parse_bytes

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><text>Login wireframe</text></svg>'
    doc_svg = parse_bytes(svg, "login.svg", "p1", "d1")
    assert "Login wireframe" in doc_svg.raw_text

    # Minimal valid 1x1 PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    doc_png = parse_bytes(png, "screen.png", "p1", "d2")
    assert "Image asset" in doc_png.raw_text
    assert "screen.png" in doc_png.raw_text
