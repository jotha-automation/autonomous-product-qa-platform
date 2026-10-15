import pytest

from src.connectors.google_drive.connector import MockGoogleDriveConnector, resolve_mock_system_root
from src.connectors.google_drive.system_root import project_id_from_folder_name


@pytest.fixture
def mock_system_root_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID", "mock-aptp-system")


def test_project_id_from_folder_name():
    assert project_id_from_folder_name("APTP-JSH-Portfolio-R1") == "JSH-PORTFOLIO-R1"


@pytest.mark.asyncio
async def test_mock_list_child_folders_under_aptp(mock_system_root_env):
    aptp = resolve_mock_system_root()
    assert aptp.is_dir(), "mock-drive APTP fixture missing"

    connector = MockGoogleDriveConnector()
    children = await connector.list_child_folders("mock-aptp-system")
    assert len(children) >= 1
    names = [c["name"] for c in children]
    assert any("Portfolio" in n or "portfolio" in n.lower() for n in names)


@pytest.mark.asyncio
async def test_mock_manifest_in_project_folder(mock_system_root_env):
    aptp = resolve_mock_system_root()
    assert aptp.is_dir(), "mock-drive APTP fixture missing"

    from src.connectors.google_drive.system_root import list_project_folders_under_system_root

    projects = await list_project_folders_under_system_root()
    portfolio = next((p for p in projects if "JSH" in p.project_id or "PORTFOLIO" in p.project_id), None)
    assert portfolio is not None
    assert portfolio.project_id == "JSH-PORTFOLIO-2026"
    assert portfolio.metadata.get("target_url")
