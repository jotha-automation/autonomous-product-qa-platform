"""Tests for run-scoped secret store."""
import json

from src.memory.enhanced_memory import EnhancedSharedMemory
from src.services.run_secret_store import RunSecretStore, identify_sensitive_columns, is_sensitive_field_name
from src.services.run_tracker import append_run_event, get_run_events


def test_register_and_get_secret():
    store = RunSecretStore.for_run("run-test-1")
    store.register("login_user.clave", "s3cret", source="test")
    assert store.get("login_user.clave") == "s3cret"
    assert "login_user_clave" in store.list_keys()
    redacted = store.redact_dict({"clave": "s3cret", "note": "ok"})
    assert redacted["clave"] == "[REDACTED]"


def test_sensitive_field_detection():
    assert is_sensitive_field_name("clave")
    assert is_sensitive_field_name("user_password")
    cols = identify_sensitive_columns(["usuario", "clave", "nombre"])
    assert cols == ["clave"]


def test_run_secrets_encrypted_at_rest():
    memory = EnhancedSharedMemory(redis_url="redis://127.0.0.1:1")
    memory._available = False
    memory.client = None
    memory._memory.clear()
    memory._fernet = None

    store = RunSecretStore.for_run("run-encrypt-1")
    store._memory = memory
    store.register("login_user.clave", "top-secret-value", source="test")

    raw_key = f"data:run_secret:run-encrypt-1:login_user_clave"
    raw_bytes = memory._memory.get(raw_key)
    assert raw_bytes is not None
    payload = json.loads(raw_bytes.decode("utf-8"))
    assert payload.get("__aptp_encrypted__") is True
    assert "top-secret-value" not in raw_bytes.decode("utf-8")

    assert store.get("login_user.clave") == "top-secret-value"


def test_append_run_event_redacts_secrets():
    run_id = "run-redact-telemetry"
    store = RunSecretStore.for_run(run_id)
    store.register("login_user.clave", "super-secret-clave", source="test")

    append_run_event(
        run_id,
        "test_event",
        "Login failed with clave super-secret-clave",
        details={"login_user.clave": "super-secret-clave", "note": "ok"},
    )
    events = get_run_events(run_id)
    assert events
    last = events[-1]
    assert "super-secret-clave" not in last["message"]
    assert "[REDACTED]" in last["message"]
    assert last["details"]["login_user.clave"] == "[REDACTED]"
    assert last["details"]["note"] == "ok"
