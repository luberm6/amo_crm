from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _ = line.split("=", 1)
        keys.add(key.strip())
    return keys


def test_root_env_templates_exist_and_use_canonical_names() -> None:
    templates = [
        REPO_ROOT / ".env.example",
        REPO_ROOT / ".env.local.example",
        REPO_ROOT / ".env.production.example",
    ]

    for path in templates:
        assert path.exists(), f"Missing env template: {path}"
        contents = path.read_text(encoding="utf-8")
        assert "MANGO_FROM_EXT" in contents
        assert "MANGO_FROM_NUMBER" not in contents
        assert "DIRECT_VOICE_STRATEGY" in contents
        assert "ADMIN_AUTH_SECRET" in contents
        assert "PROVIDER_SETTINGS_SECRET" in contents
        assert "MEDIA_GATEWAY_ENABLED" in contents
        assert "GEMINI_API_KEY" in contents
        assert "ELEVENLABS_API_KEY" in contents

    for path in templates[:2]:
        contents = path.read_text(encoding="utf-8")
        assert "127.0.0.1:5433" in contents
        assert "localhost:5432" not in contents


def test_production_env_template_has_no_stray_change_me_line() -> None:
    production_template = REPO_ROOT / ".env.production.example"
    lines = [line.strip() for line in production_template.read_text(encoding="utf-8").splitlines()]
    assert "CHANGE_ME" not in lines


def test_admin_panel_env_template_exists_and_exposes_only_vite_api_base_url() -> None:
    env_path = REPO_ROOT / "admin-panel" / ".env.example"
    assert env_path.exists()
    keys = parse_env_keys(env_path)
    assert keys == {"VITE_API_BASE_URL"}
