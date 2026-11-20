"""Phone app updates without the Play Store.

An admin publishes a new APK (from the Mac: `./run-android.sh publish`, or Settings → Phone app on the web). The
app asks /app/android/latest when it opens; if the published build is newer than its own it offers to download it
and opens Android's installer, which updates the app in place (same signing key, so data and sign-in are kept).
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.deps import Ctx, get_current_user, require
from app.core.errors import AppError, not_found
from app.modules.identity.models import User

router = APIRouter(prefix="/app", tags=["app updates"])
KEEP = 3  # older APKs kept, in case one has to be put back
MAX_BYTES = 200 * 1024 * 1024


def folder() -> Path:
    p = Path(os.path.expanduser(get_settings().app_release_dir))
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest() -> dict | None:
    f = folder() / "latest.json"
    if not f.exists():
        return None
    info = json.loads(f.read_text(encoding="utf-8"))
    return info if (folder() / info["file"]).exists() else None


@router.get("/android/latest")
def android_latest(user: User = Depends(get_current_user)) -> dict | None:
    """The newest published build, or null when none has been published."""
    info = latest()
    if info is None:
        return None
    return {**info, "download_url": "/api/v1/app/android/download"}


@router.get("/android/download")
def android_download(user: User = Depends(get_current_user)):
    info = latest()
    if info is None:
        raise not_found("App update")
    return FileResponse(folder() / info["file"], media_type="application/vnd.android.package-archive",
                        filename=f"TayalERP-{info['version_name']}.apk")


@router.post("/android", status_code=201)
async def android_publish(file: UploadFile = File(...), version_code: int = Form(..., ge=1),
                          version_name: str = Form(..., min_length=1, max_length=30),
                          notes: str | None = Form(default=None, max_length=1000),
                          ctx: Ctx = Depends(require("admin.settings"))) -> dict:
    """Publish a new build. Its version code (the number after + in pubspec's version) must be higher than the
    one already published, or phones would not see it as an update."""
    if not re.fullmatch(r"[0-9A-Za-z.+_-]+", version_name):
        raise AppError(422, "VALIDATION_ERROR", "Version name: digits, letters and . + - _ only")
    cur = latest()
    if cur and version_code <= int(cur["version_code"]):
        raise AppError(409, "OLD_VERSION", f"Build {version_code} is not newer than the published build "
                       f"{cur['version_code']} ({cur['version_name']}). Build again with a higher number.")
    d = folder()
    h, size = hashlib.sha256(), 0
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            first = True
            while chunk := await file.read(1024 * 1024):
                if first and not chunk.startswith(b"PK"):
                    raise AppError(422, "NOT_AN_APK", "That file is not an Android app (.apk)")
                first = False
                size += len(chunk)
                if size > MAX_BYTES:
                    raise AppError(413, "TOO_BIG", "The file is too big for an app")
                h.update(chunk)
                out.write(chunk)
        if size == 0:
            raise AppError(422, "NOT_AN_APK", "The file is empty")
        name = f"tayal-erp-{version_code}.apk"
        os.replace(tmp, d / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    info = {"version_code": version_code, "version_name": version_name, "notes": notes or None, "file": name,
            "size": size, "sha256": h.hexdigest(), "published_at": datetime.now(UTC).isoformat(),
            "published_by": ctx.user.full_name}
    (d / "latest.json.part").write_text(json.dumps(info, indent=1), encoding="utf-8")
    os.replace(d / "latest.json.part", d / "latest.json")
    old = sorted(d.glob("tayal-erp-*.apk"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    for p in old[:-KEEP]:
        p.unlink(missing_ok=True)
    return info
