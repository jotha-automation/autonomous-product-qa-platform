from src.intake.file_types import (
    SUPPORTED_EXTENSIONS,
    guess_mime_from_filename,
    is_intake_file,
)


def test_supported_extensions_include_user_formats():
    for ext in (".xlsx", ".docx", ".pdf", ".md", ".txt", ".jpg", ".png", ".jpeg", ".svg"):
        assert ext in SUPPORTED_EXTENSIONS


def test_is_intake_file_accepts_documents_and_images():
    assert is_intake_file("spec.pdf")
    assert is_intake_file("wire.png", "image/png")
    assert is_intake_file("flow.svg", "image/svg+xml")
    assert is_intake_file("notes.md")


def test_is_intake_file_rejects_zip_and_unknown_google_types():
    assert not is_intake_file("bundle.zip")
    assert not is_intake_file("form.gform", "application/vnd.google-apps.form")


def test_is_intake_file_accepts_google_docs_export():
    assert is_intake_file(
        "BRD",
        "application/vnd.google-apps.document",
    )


def test_guess_mime():
    assert guess_mime_from_filename("a.PNG") == "image/png"
    assert guess_mime_from_filename("b.docx").startswith("application/vnd.openxml")
