from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.core.management.base import CommandError

from apps.access_control.models import UserRole


@dataclass(frozen=True)
class DemoUserSpec:
    key: str
    email: str
    external_id: str
    role: str


DEMO_USER_SPECS: dict[str, DemoUserSpec] = {
    "administrator": DemoUserSpec(
        key="administrator",
        email="demo.administrator@hitech.local",
        external_id="demo-administrator-external-id",
        role=UserRole.ADMINISTRATOR,
    ),
    "project_manager": DemoUserSpec(
        key="project_manager",
        email="demo.project-manager@hitech.local",
        external_id="demo-project-manager-external-id",
        role=UserRole.PROJECT_MANAGER,
    ),
    "survey_engineer": DemoUserSpec(
        key="survey_engineer",
        email="demo.survey-engineer@hitech.local",
        external_id="demo-survey-engineer-external-id",
        role=UserRole.SURVEY_ENGINEER,
    ),
    "viewer": DemoUserSpec(
        key="viewer",
        email="demo.viewer@hitech.local",
        external_id="demo-viewer-external-id",
        role=UserRole.VIEWER,
    ),
}


def ensure_demo_auth_enabled() -> None:
    if not settings.ENABLE_DEMO_AUTH:
        raise CommandError("Assessment demo access is disabled. Set ENABLE_DEMO_AUTH=True.")


def get_demo_private_key_path() -> Path:
    return Path(settings.DEMO_AUTH_PRIVATE_KEY_PATH)


def get_demo_public_key_path() -> Path:
    return Path(settings.DEMO_AUTH_PUBLIC_KEY_PATH)


def ensure_demo_keypair(*, rotate: bool = False) -> tuple[Path, Path]:
    ensure_demo_auth_enabled()

    private_key_path = get_demo_private_key_path()
    public_key_path = get_demo_public_key_path()
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.parent.mkdir(parents=True, exist_ok=True)

    if not rotate and private_key_path.exists() and public_key_path.exists():
        return private_key_path, public_key_path

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_key_path.write_bytes(private_key_pem)
    public_key_path.write_bytes(public_key_pem)
    return private_key_path, public_key_path


def issue_demo_token_for_spec(*, spec: DemoUserSpec, lifetime_seconds: int | None = None) -> str:
    ensure_demo_auth_enabled()
    ttl_seconds = max(
        60,
        min(
            lifetime_seconds or settings.DEMO_AUTH_TOKEN_TTL_SECONDS,
            settings.DEMO_AUTH_TOKEN_TTL_SECONDS,
        ),
    )
    now = datetime.now(timezone.utc)
    payload = {
        "sub": spec.external_id,
        "email": spec.email,
        "role": spec.role,
        "exp": now + timedelta(seconds=ttl_seconds),
        "iat": now,
    }
    return jwt.encode(payload, _load_demo_private_key(), algorithm="RS256")


def get_demo_user_spec(role_key: str) -> DemoUserSpec | None:
    return DEMO_USER_SPECS.get(role_key)


def _load_demo_private_key() -> str:
    private_key_value = _normalize_pem_value(settings.DEMO_AUTH_PRIVATE_KEY)
    if private_key_value:
        return private_key_value

    private_key_path = get_demo_private_key_path()
    if not private_key_path.exists():
        raise CommandError(
            f"Demo private key not found at {private_key_path}. Run `python manage.py init_demo_auth_keys` first."
        )
    return private_key_path.read_text(encoding="utf-8")


def _normalize_pem_value(value: str) -> str:
    return value.strip().replace("\\n", "\n")
