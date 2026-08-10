import logging

from celery import shared_task

from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.models import Project
from apps.surveys.models import Survey

logger = logging.getLogger(__name__)


@shared_task(name="apps.audit.record_file_download_audit_event")
def record_file_download_audit_event(*, user_id: int, project_id: int, survey_id: int, survey_file_id: int):
    project = Project.objects.get(pk=project_id)
    survey = Survey.objects.get(pk=survey_id)
    user = None

    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.get(pk=user_id)

    record_audit_event(
        action=AuditAction.FILE_DOWNLOADED,
        entity_type="survey_file",
        entity_id=survey_file_id,
        user=user,
        project=project,
        survey=survey,
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
