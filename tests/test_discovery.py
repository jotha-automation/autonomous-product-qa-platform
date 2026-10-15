from pathlib import Path

import pytest

from src.intake.discovery import (
    classify_document,
    discover_local_tree,
)
from src.intake.schemas.canonical import DocumentTypeEnum
from types import SimpleNamespace

from src.services.intake_registry import compute_file_changes, compute_folder_changes


FIXTURE_PROJECT_ROOT = (
    Path(__file__).parent / "fixtures" / "mock-drive" / "APTP" / "APTP-JSH-Portfolio-R1"
)


def test_classify_by_folder_scaffolding():
    assert classify_document("01-Business-Requirements/BRD-JSH-Portfolio-v1.2.md", "BRD-JSH-Portfolio-v1.2.md") == DocumentTypeEnum.BRD
    assert classify_document("02-Product-Requirements/PRD-JSH-Portfolio-R1.md", "PRD-JSH-Portfolio-R1.md") == DocumentTypeEnum.PRD
    assert classify_document("04-Use-Cases/use-cases.md", "use-cases.md") == DocumentTypeEnum.UC
    assert classify_document("05-Acceptance-Criteria/acceptance-criteria.md", "acceptance-criteria.md") == DocumentTypeEnum.AC
    assert classify_document("intake/brd.txt", "brd.txt") == DocumentTypeEnum.BRD


def test_discover_portfolio_webapp_tree():
    assert FIXTURE_PROJECT_ROOT.is_dir(), "mock-drive fixture project missing"
    result = discover_local_tree(FIXTURE_PROJECT_ROOT, root_id="mock-drive-jsh-portfolio-r1")
    assert len(result.files) >= 4
    assert len(result.folders) >= 3

    types = {f.inferred_doc_type for f in result.files}
    assert DocumentTypeEnum.BRD in types
    assert DocumentTypeEnum.PRD in types
    assert DocumentTypeEnum.UC in types
    assert DocumentTypeEnum.AC in types

    mandatory = result.best_mandatory_set()
    assert len(mandatory) == 4


def test_registry_file_diff_detects_new_and_modified():
    from src.intake.discovery import DiscoveredFile, DiscoverySource, build_fingerprint

    prev = {
        "intake/brd.txt": SimpleNamespace(
            drive_file_id="f1",
            relative_path="intake/brd.txt",
            content_fingerprint=build_fingerprint("100", 10, "f1"),
        )
    }
    current = [
        DiscoveredFile(
            file_id="f1",
            name="brd.txt",
            relative_path="intake/brd.txt",
            fingerprint=build_fingerprint("100", 10, "f1"),
            inferred_doc_type=DocumentTypeEnum.BRD,
            source=DiscoverySource.LOCAL,
        ),
        DiscoveredFile(
            file_id="f2",
            name="prd.txt",
            relative_path="intake/prd.txt",
            fingerprint=build_fingerprint("200", 20, "f2"),
            inferred_doc_type=DocumentTypeEnum.PRD,
            source=DiscoverySource.LOCAL,
        ),
    ]
    changes, to_process = compute_file_changes(prev, current)
    event_types = {c.event_type.value for c in changes}
    assert "file_created" in event_types
    assert len(to_process) == 1
    assert to_process[0].relative_path == "intake/prd.txt"


def test_registry_folder_diff():
    changes = compute_folder_changes(
        {"01-Business-Requirements"},
        {"01-Business-Requirements", "02-Product-Requirements"},
    )
    assert len(changes) == 1
    assert changes[0].event_type.value == "folder_created"
