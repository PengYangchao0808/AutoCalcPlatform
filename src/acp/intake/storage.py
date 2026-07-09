from __future__ import annotations

import shutil
import uuid
from pathlib import Path

_UPLOAD_DIR_NAME = "_uploads"
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class UploadStorage:
    def __init__(self, run_root: Path | str):
        self.run_root = Path(run_root).resolve()

    def upload_dir(self, project_id: str, upload_id: str) -> Path:
        d = self.run_root / project_id / _UPLOAD_DIR_NAME / upload_id
        return d

    def original_dir(self, project_id: str, upload_id: str) -> Path:
        return self.upload_dir(project_id, upload_id) / "original"

    def normalized_dir(self, project_id: str, upload_id: str) -> Path:
        return self.upload_dir(project_id, upload_id) / "normalized"

    def save_upload(
        self,
        project_id: str,
        filename: str,
        content: bytes,
    ) -> tuple[str, Path]:
        if len(content) > _MAX_UPLOAD_BYTES:
            raise ValueError(f"File too large: {len(content)} bytes (max {_MAX_UPLOAD_BYTES})")
        if not _is_safe_filename(filename):
            raise ValueError(f"Unsafe filename: {filename}")
        upload_id = f"up_{uuid.uuid4().hex[:12]}"
        orig_dir = self.original_dir(project_id, upload_id)
        orig_dir.mkdir(parents=True, exist_ok=True)
        dest = orig_dir / filename
        dest.write_bytes(content)
        return upload_id, dest

    def save_normalized(self, project_id: str, upload_id: str, name: str, text: str) -> Path:
        norm_dir = self.normalized_dir(project_id, upload_id)
        norm_dir.mkdir(parents=True, exist_ok=True)
        safe_name = name if _is_safe_filename(name) else "structure.xyz"
        dest = norm_dir / safe_name
        dest.write_text(text, encoding="utf-8")
        return dest


def _is_safe_filename(name: str) -> bool:
    if not name or len(name) > 255:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if name.startswith("."):
        return False
    return True


__all__ = ["UploadStorage"]
