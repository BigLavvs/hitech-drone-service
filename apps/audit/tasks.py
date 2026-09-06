import logging

from celery import shared_task

from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event

logger = logging.getLogger(__name__)


@shared_task(name="apps.audit.record_file_download_audit_event")
def record_file_download_audit_event(*, user_id: int, project_id: int, survey_id: int, survey_file_id: int):
    from apps.access_control.services import get_local_user_by_id

    user = get_local_user_by_id(user_id=user_id)

    record_audit_event(
        action=AuditAction.FILE_DOWNLOADED,
        entity_type="survey_file",
        entity_id=survey_file_id,
        user=user,
        project_id=project_id,
        survey_id=survey_id,
    )


def dispatch_file_download_audit_event(*, user_id: int, project_id: int, survey_id: int, survey_file_id: int):
    try:
        record_file_download_audit_event.delay(
            user_id=user_id,
            project_id=project_id,
            survey_id=survey_id,
            survey_file_id=survey_file_id,
        )
    except Exception:
        logger.warning(
            "File download audit dispatch unavailable; continuing without blocking download redirect.",
            extra={
                "user_id": user_id,
                "project_id": project_id,
                "survey_id": survey_id,
                "survey_file_id": survey_file_id,
            },
        )
