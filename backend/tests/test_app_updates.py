"""Publishing a phone app update and phones fetching it."""

import io

import pytest

from app.core.config import get_settings

API = "/api/v1/app/android"
APK = b"PK\x03\x04" + b"x" * 5000


@pytest.fixture
def releases(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_release_dir", str(tmp_path))
    return tmp_path


def _publish(world, user, co, code, name="1.0", data=APK):
    return world.client.post(API, headers=world.headers(user, co), data={"version_code": str(code),
                             "version_name": name, "notes": "Faster dispatch"},
                             files={"file": ("app-release.apk", io.BytesIO(data), "application/octet-stream")})


def test_publish_then_phone_downloads(world, releases):
    co = world.company()
    admin = world.user(co, ["SUPER_ADMIN"])
    sales = world.user(co, ["SALES_EXECUTIVE"])
    assert world.client.get(f"{API}/latest", headers=world.headers(sales, co)).json() is None
    assert _publish(world, sales, co, 2).status_code == 403  # only admins publish
    r = _publish(world, admin, co, 2, "0.2.0")
    assert r.status_code == 201, r.text
    info = world.client.get(f"{API}/latest", headers=world.headers(sales, co)).json()
    assert info["version_code"] == 2 and info["version_name"] == "0.2.0" and info["size"] == len(APK)
    dl = world.client.get(info["download_url"].replace("/api/v1/app/android", API), headers=world.headers(sales, co))
    assert dl.status_code == 200 and dl.content == APK
    assert dl.headers["content-type"] == "application/vnd.android.package-archive"
    # not newer → refused; not an APK → refused
    assert _publish(world, admin, co, 2).json()["code"] == "OLD_VERSION"
    assert _publish(world, admin, co, 3, data=b"hello").json()["code"] == "NOT_AN_APK"
    # only the last 3 are kept
    for c in (3, 4, 5, 6):
        assert _publish(world, admin, co, c).status_code == 201
    assert sorted(p.name for p in releases.glob("*.apk")) == ["tayal-erp-4.apk", "tayal-erp-5.apk", "tayal-erp-6.apk"]


def test_needs_sign_in(world, releases):
    assert world.client.get(f"{API}/latest").status_code == 401
