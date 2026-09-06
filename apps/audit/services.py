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
    project_id=None,
    survey_id=None,
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
        project_id=project_id if project_id is not None else getattr(project, "pk", None),
        survey_id=survey_id if survey_id is not None else getattr(survey, "pk", None),
        details=details,
        ip_address=ip_address,
    )


def get_audit_logs_visible_to_user(*, user: User, filters=None):
    queryset = AuditLog.objects.order_by("-timestamp", "-id")

    if not user.is_active:
        return queryset.none()

    if user.role == UserRole.ADMINISTRATOR:
        pass
    elif user.role in {
        UserRole.PROJECT_MANAGER,
        UserRole.SURVEY_ENGINEER,
        UserRole.VIEWER,
    }:
        from apps.projects.services import get_project_ids_visible_to_user

        queryset = queryset.filter(project_id__in=get_project_ids_visible_to_user(user=user))
    else:
        return queryset.none()

    filters = filters or {}
    for field_name in ("project_id", "survey_id", "action"):
        if field_name in filters:
            queryset = queryset.filter(**{field_name: filters[field_name]})
    if "from_date" in filters:
        queryset = queryset.filter(timestamp__date__gte=filters["from_date"])
    if "to_date" in filters:
        queryset = queryset.filter(timestamp__date__lte=filters["to_date"])
    return queryset


def get_audit_log_visible_to_user(*, user: User, audit_log_id: int) -> AuditLog:
    audit_log = AuditLog.objects.filter(pk=audit_log_id).first()
    if audit_log is None:
        raise AuditLog.DoesNotExist

    if not get_audit_logs_visible_to_user(user=user).filter(pk=audit_log_id).exists():
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have permission to access this audit log.")

    return audit_log
