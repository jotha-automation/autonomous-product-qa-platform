"""Poll the APTP system root — sync all project subfolders on change."""
import asyncio
import logging
import os
from typing import Optional

from src.connectors.google_drive.system_root import get_system_root_folder_id, sync_system_root
from src.runtime_policy import drive_enabled

logger = logging.getLogger("drive_watcher")

_watcher_task: Optional[asyncio.Task] = None
_sync_lock: asyncio.Lock = asyncio.Lock()
_pending_change: bool = False


async def _poll_loop(poll_interval: int) -> None:
    trigger_run = os.getenv("APTP_DRIVE_WATCH_TRIGGER_RUN", "false").lower() in {"1", "true", "yes"}
    mode = os.getenv("APTP_DRIVE_DEFAULT_MODE", "full")
    debounce_seconds = max(0, int(os.getenv("APTP_DRIVE_WATCH_DEBOUNCE_SECONDS", "5")))

    global _pending_change

    while True:
        try:
            if _sync_lock.locked():
                _pending_change = True
                logger.debug("Drive sync already running; deferring to next poll")
            else:
                async with _sync_lock:
                    result = await sync_system_root(trigger_run=trigger_run, mode=mode)
                    _pending_change = False
                    processed = [p for p in result.projects if p.files_to_process and not p.skipped]
                    if processed:
                        logger.info(
                            "System root sync: %d/%d projects had changes",
                            len(processed),
                            result.project_folders_found,
                        )
                    if result.new_project_folders:
                        logger.info("New project folders under APTP: %s", result.new_project_folders)
                    if processed and debounce_seconds:
                        await asyncio.sleep(debounce_seconds)
        except Exception as exc:
            logger.error("Drive system-root watcher failed: %s", exc)

        await asyncio.sleep(poll_interval)


def start_drive_watcher() -> Optional[asyncio.Task]:
    """Poll GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID (APTP) when APTP_DRIVE_WATCH_ENABLED=true."""
    global _watcher_task

    if not drive_enabled():
        logger.info("Drive watcher disabled by APTP_DRIVE_ENABLED=false")
        return None

    enabled = os.getenv("APTP_DRIVE_WATCH_ENABLED", "false").lower() in {"1", "true", "yes"}
    if not enabled:
        return None

    try:
        root_id = get_system_root_folder_id()
    except ValueError as exc:
        logger.warning("Drive watcher not started: %s", exc)
        return None

    poll_interval = max(60, int(os.getenv("APTP_DRIVE_POLL_INTERVAL", "300")))

    loop = asyncio.get_running_loop()
    _watcher_task = loop.create_task(_poll_loop(poll_interval))
    logger.info(
        "Drive watcher started on system root %s (%s) interval=%ds",
        os.getenv("GOOGLE_DRIVE_SYSTEM_ROOT_NAME", "APTP"),
        root_id,
        poll_interval,
    )
    return _watcher_task


async def stop_drive_watcher() -> None:
    global _watcher_task
    if _watcher_task and not _watcher_task.done():
        _watcher_task.cancel()
        try:
            await _watcher_task
        except asyncio.CancelledError:
            pass
    _watcher_task = None
