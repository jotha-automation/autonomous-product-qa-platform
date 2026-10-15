from pathlib import Path
import os
import stat

from src.services.project_env import merge_project_env, origin_base_url, read_env_file, strip_secret_env_vars


def test_origin_base_url_strips_path():
    assert origin_base_url("https://bank-demo.up.railway.app/login") == "https://bank-demo.up.railway.app"


def test_merge_project_env_preserves_secrets(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "BASE_URL=https://old.example.com\nAPTP_SECRET_LOGIN_USER_CLAVE=secret123\n",
        encoding="utf-8",
    )
    merge_project_env(env_path, {"BASE_URL": "https://new.example.com"})
    values = read_env_file(env_path)
    assert values["BASE_URL"] == "https://new.example.com"
    assert values["APTP_SECRET_LOGIN_USER_CLAVE"] == "secret123"


def test_write_env_file_uses_restrictive_permissions(tmp_path):
    env_path = tmp_path / ".env"
    merge_project_env(env_path, {"BASE_URL": "https://example.com"})
    mode = stat.S_IMODE(os.stat(env_path).st_mode)
    assert mode == 0o600


def test_strip_secret_env_vars_removes_secrets_keeps_base_url(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "BASE_URL=https://bank.example.com\n"
        "APTP_RUN_ID=run-1\n"
        "APTP_SECRET_LOGIN_USER_CLAVE=secret123\n"
        "OTHER=value\n",
        encoding="utf-8",
    )
    strip_secret_env_vars(tmp_path)
    values = read_env_file(env_path)
    assert "APTP_SECRET_LOGIN_USER_CLAVE" not in values
    assert values["BASE_URL"] == "https://bank.example.com"
    assert values["APTP_RUN_ID"] == "run-1"
    assert values["OTHER"] == "value"
