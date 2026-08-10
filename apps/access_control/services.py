from django.db import transaction

from django.core.exceptions import PermissionDenied, ValidationError

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.models import Project
from apps.access_control.demo_access import DemoUserSpec

_UNSET = object()


def resolve_active_user_from_external_identity(
    *,
    external_id: str,
    email: str,
    role: str,
) -> User | None:
    with transaction.atomic():
        user = (
            User.objects.select_for_update()
            .filter(external_id=external_id)
            .first()
        )

        if user is None or not user.is_active:
            return None

        updated_fields: list[str] = []

        if user.email != email:
            user.email = email
            updated_fields.append("email")

        if user.role != role:
            user.role = role
            updated_fields.append("role")

        if updated_fields:
            user.save(update_fields=[*updated_fields, "updated_at"])

        return user


def get_seeded_demo_user(*, spec: DemoUserSpec) -> User | None:
    return User.objects.filter(
        external_id=spec.external_id,
        email=spec.email,
        role=spec.role,
        is_active=True,
    ).first()


def get_local_users_for_admin(*, actor: User):
    _require_administrator(actor)
    return User.objects.order_by("id")


def get_local_user_for_admin(*, actor: User, user_id: int) -> User:
    _require_administrator(actor)
    return User.objects.get(pk=user_id)


def create_local_user(
    *,
    actor: User,
    email: str,
    external_id: str,
    role: str,
    is_active: bool = True,
) -> User:
    _require_administrator(actor)

    if User.objects.filter(email__iexact=email).exists():
        raise ValidationError({"email": ["A user with this email already exists."]})

    if User.objects.filter(external_id=external_id).exists():
        raise ValidationError({"external_id": ["A user with this external_id already exists."]})

    with transaction.atomic():
        user = User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            is_active=is_active,
            is_staff=role == UserRole.ADMINISTRATOR,
        )
        record_audit_event(
            action=AuditAction.ADMIN_ACTION,
            entity_type="user",
            entity_id=user.pk,
            user=actor,
            details={
                "operation": "user_created",
                "user_id": user.pk,
                "email": user.email,
                "external_id": user.external_id,
                "role": user.role,
                "is_active": user.is_active,
            },
        )

    return user


def update_local_user(
    *,
    actor: User,
    target_user: User,
    email: str | object = _UNSET,
    role: str | object = _UNSET,
    is_active: bool | object = _UNSET,
) -> User:
    _require_administrator(actor)

    next_email = target_user.email if email is _UNSET else email
    next_role = target_user.role if role is _UNSET else role
    next_is_active = target_user.is_active if is_active is _UNSET else is_active

    if (
        next_email.lower() != target_user.email.lower()
        and User.objects.filter(email__iexact=next_email).exclude(pk=target_user.pk).exists()
    ):
        raise ValidationError({"email": ["A user with this email already exists."]})

    _ensure_project_manager_ownership_preserved(
        target_user=target_user,
        next_role=next_role,
        next_is_active=next_is_active,
    )

    update_fields: list[str] = []
    audit_changes: dict[str, object] = {}

    if email is not _UNSET and next_email != target_user.email:
        target_user.email = next_email
        update_fields.append("email")
        audit_changes["email"] = next_email

    if role is not _UNSET and next_role != target_user.role:
        target_user.role = next_role
        target_user.is_staff = next_role == UserRole.ADMINISTRATOR
        update_fields.extend(["role", "is_staff"])
        audit_changes["role"] = next_role

    if is_active is not _UNSET and next_is_active != target_user.is_active:
        target_user.is_active = next_is_active
        update_fields.append("is_active")
        audit_changes["is_active"] = next_is_active

    if not update_fields:
        return target_user

    with transaction.atomic():
        target_user.save(update_fields=[*sorted(set(update_fields)), "updated_at"])
        record_audit_event(
            action=AuditAction.ADMIN_ACTION,
            entity_type="user",
            entity_id=target_user.pk,
            user=actor,
            details={
                "operation": "user_updated",
                "user_id": target_user.pk,
                "changes": audit_changes,
            },
        )

    return target_user


def _require_administrator(user: User) -> None:
    if not user.is_active or user.role != UserRole.ADMINISTRATOR:
        raise PermissionDenied("Only active administrators can manage local users.")


def _ensure_project_manager_ownership_preserved(
    *,
    target_user: User,
    next_role: str,
    next_is_active: bool,
) -> None:
    owns_projects = Project.objects.filter(project_manager=target_user).exists()
    if not owns_projects:
        return

    if next_role != UserRole.PROJECT_MANAGER or not next_is_active:
        raise ValidationError(
            {
                "role": [
                    "Transfer owned projects through the existing project workflow before changing this user."
                ],
                "is_active": [
                    "Transfer owned projects through the existing project workflow before deactivating this user."
                ],
            }
        )
