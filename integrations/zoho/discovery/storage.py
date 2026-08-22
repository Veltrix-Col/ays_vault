from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .constants import ALLOWED_PROFILES, SNAPSHOT_FILES


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _semantic_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: snapshot[key]
        for key in (
            "organization", "modules", "fields", "layouts", "relationships",
            "related_lists", "subforms", "picklists", "errors",
        )
    }
    manifest = snapshot["manifest"]
    payload["manifest_technical"] = {
        key: manifest.get(key)
        for key in (
            "schema_version", "profile", "environment", "status", "sdk_version",
            "api_version", "backend", "organization_id", "modules_total",
            "modules_fields_ok", "modules_fields_failed", "relationships_total",
            "subforms_total", "layouts_total", "related_lists_total", "errors_total",
            "read_only", "source",
        )
    }
    return payload


def semantic_digest(snapshot: dict[str, Any]) -> str:
    serialized = json.dumps(
        _semantic_payload(snapshot), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def load_snapshot(path: Path) -> dict[str, Any]:
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    result: dict[str, Any] = {"manifest": manifest}
    for filename in SNAPSHOT_FILES:
        key = filename.removesuffix(".json")
        result[key] = json.loads((path / filename).read_text(encoding="utf-8"))[key]
    return result


class SnapshotStore:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def save(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self._validate_snapshot(snapshot)
        profile = str(snapshot["manifest"].get("profile") or "")
        if profile not in ALLOWED_PROFILES:
            raise ValueError("Perfil de snapshot no permitido.")
        digest = semantic_digest(snapshot)
        snapshot["manifest"]["semantic_digest"] = digest
        profile_root = self.root / profile
        latest = profile_root / "latest"
        history = profile_root / "history"
        has_current_snapshot = (latest / "manifest.json").is_file()
        if has_current_snapshot:
            current = load_snapshot(latest)
            if current["manifest"].get("semantic_digest") == digest:
                return {"path": latest, "changed": False, "history_path": None}

        profile_root.mkdir(parents=True, exist_ok=True)
        staging = profile_root / f".latest.{uuid.uuid4().hex}.tmp"
        staging.mkdir()
        history_path = None
        try:
            self._write_directory(staging, snapshot)
            # Prove that every JSON is complete and loadable before latest/history
            # are touched. This is the minimum publish boundary.
            staged = load_snapshot(staging)
            self._validate_snapshot(staged)
            if semantic_digest(staged) != digest:
                raise ValueError("El snapshot temporal no conserva su digest semántico.")
            if has_current_snapshot:
                generated = str(
                    json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
                    .get("generated_at", "unknown")
                )
                safe_generated = "".join(
                    character for character in generated
                    if character.isdigit() or character in {"T", "Z"}
                ) or "unknown"
                history.mkdir(parents=True, exist_ok=True)
                history_path = history / safe_generated
                counter = 1
                while history_path.exists():
                    history_path = history / f"{safe_generated}-{counter}"
                    counter += 1
                os.replace(latest, history_path)
            elif latest.exists():
                # Keep the tracked placeholder recoverable until publication.
                placeholder_backup = profile_root / f".previous.{uuid.uuid4().hex}.tmp"
                os.replace(latest, placeholder_backup)
                try:
                    os.replace(staging, latest)
                except BaseException:
                    os.replace(placeholder_backup, latest)
                    raise
                shutil.rmtree(placeholder_backup, ignore_errors=True)
                return {"path": latest, "changed": True, "history_path": None}
            try:
                os.replace(staging, latest)
            except BaseException:
                if history_path is not None and history_path.exists() and not latest.exists():
                    os.replace(history_path, latest)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return {"path": latest, "changed": True, "history_path": history_path}

    @staticmethod
    def _validate_snapshot(snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("manifest"), dict):
            raise ValueError("Snapshot sin manifest válido.")
        manifest = snapshot["manifest"]
        required_manifest = {
            "schema_version", "profile", "environment", "status", "modules_total",
            "modules_fields_ok", "modules_fields_failed", "errors_total", "read_only",
        }
        if required_manifest - manifest.keys():
            raise ValueError("Manifest incompleto.")
        if manifest.get("status") not in {"success", "partial"}:
            raise ValueError("Estado de snapshot no publicable.")
        if manifest.get("profile") != manifest.get("environment"):
            raise ValueError("Perfil y entorno del snapshot no coinciden.")
        if manifest.get("read_only") is not True:
            raise ValueError("El snapshot no declara modo read-only.")
        for filename in SNAPSHOT_FILES:
            key = filename.removesuffix(".json")
            if key not in snapshot or not isinstance(snapshot[key], (list, dict)):
                raise ValueError(f"Snapshot sin colección válida: {key}.")
        if manifest.get("modules_total") != len(snapshot["modules"]):
            raise ValueError("El conteo de módulos no coincide.")
        if manifest.get("errors_total") != len(snapshot["errors"]):
            raise ValueError("El conteo de errores no coincide.")

    @staticmethod
    def _write_directory(path: Path, snapshot: dict[str, Any]) -> None:
        write_json(path / "manifest.json", snapshot["manifest"])
        for filename in SNAPSHOT_FILES:
            key = filename.removesuffix(".json")
            write_json(path / filename, {
                "schema_version": snapshot["manifest"]["schema_version"],
                "profile": snapshot["manifest"]["profile"],
                key: snapshot[key],
            })
