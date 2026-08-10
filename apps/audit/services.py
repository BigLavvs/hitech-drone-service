from django.core.exceptions import ValidationError

from apps.audit.models import AuditAction, AuditLog
from apps.access_control.models import User, UserRole


def record_audit_event(
    *,
    action: AuditAction | str,
    entity_type: str,
    entity_id: int,
    user=None,
    project=None,
    survey=None,
    details=None,
    ip_address=None,
) -> AuditLog:
    try:
        normalized_action = AuditAction(action).value
    except ValueError as exc:
        raise ValidationError("Invalid audit action.") from exc

    return AuditLog.objects.create(
        action=normalized_action,
        entity_type=entity_type,
        entity_id=entity_id,
        user=user,
        project=project,
        survey=survey,
        details=details,
        ip_address=ip_address,
    )


def get_audit_logs_visible_to_user(*, user: User):
    queryset = AuditLog.objects.select_related("user", "project", "survey").order_by("-timestamp", "-id")

    if not user.is_active:
        return queryset.none()

    if user.role == UserRole.ADMINISTRATOR:
        return queryset

    if user.role == UserRole.PROJECT_MANAGER:
        return queryset.filter(project__project_manager=user)

    if user.role in {UserRole.SURVEY_ENGINEER, UserRole.VIEWER}:
        return queryset.filter(project__memberships__user=user).distinct()

    return queryset.none()


def get_audit_log_visible_to_user(*, user: User, audit_log_id: int) -> AuditLog:
    audit_log = AuditLog.objects.select_related("user", "project", "survey").filter(pk=audit_log_id).first()
    if audit_log is None:
        raise AuditLog.DoesNotExist

    if not get_audit_logs_visible_to_user(user=user).filter(pk=audit_log_id).exists():
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have permission to access this audit log.")

    return audit_log
