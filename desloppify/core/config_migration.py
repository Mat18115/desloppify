"""Legacy state-file → config.json migration helpers."""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from desloppify.core.fallbacks import log_best_effort_failure
from desloppify.core.discovery_api import safe_write_text

logger = logging.getLogger(__name__)


def _merge_config_value(config: dict, key: str, value: object) -> None:
    """Merge a config value into the target dict."""
    if key not in config:
        config[key] = copy.deepcopy(value)
        return
    if isinstance(value, list) and isinstance(config[key], list):
        for item in value:
            if item not in config[key]:
                config[key].append(item)
        return
    if isinstance(value, dict) and isinstance(config[key], dict):
        for dk, dv in value.items():
            if dk not in config[key]:
                config[key][dk] = copy.deepcopy(dv)
        return


def _load_state_file_payload(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.debug("Skipping unreadable state file %s: %s", path, exc)
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _merge_legacy_state_config(config: dict, old_config: dict) -> None:
    from desloppify.core.config import CONFIG_SCHEMA

    for key, value in old_config.items():
        if key not in CONFIG_SCHEMA:
            continue
        _merge_config_value(config, key, value)


def _strip_config_from_state_file(path: Path, state_data: dict) -> None:
    if "config" not in state_data:
        return
    del state_data["config"]
    try:
        safe_write_text(path, json.dumps(state_data, indent=2) + "\n")
    except OSError as exc:
        log_best_effort_failure(
            logger,
            f"rewrite state file {path} after config migration",
            exc,
        )


def _migrate_single_state_file(config: dict, path: Path) -> bool:
    state_data = _load_state_file_payload(path)
    if state_data is None:
        return False
    old_config = state_data.get("config")
    if not isinstance(old_config, dict) or not old_config:
        return False

    _merge_legacy_state_config(config, old_config)
    _strip_config_from_state_file(path, state_data)
    return True


def migrate_from_state_files(config_path: Path) -> dict:
    """Migrate config keys from state-*.json files into config.json.

    Reads state["config"] from all state files, merges them (union for
    lists, merge for dicts), writes config.json, and strips "config" from
    the state files.
    """
    from desloppify.core.config import save_config

    config: dict = {}
    state_dir = config_path.parent
    if not state_dir.exists():
        return config

    state_files = list(state_dir.glob("state-*.json")) + list(
        state_dir.glob("state.json")
    )
    migrated_any = False
    for sf in state_files:
        migrated_any = _migrate_single_state_file(config, sf) or migrated_any

    if migrated_any and config:
        try:
            save_config(config, config_path)
        except OSError as exc:
            log_best_effort_failure(
                logger, f"save migrated config to {config_path}", exc
            )

    return config
