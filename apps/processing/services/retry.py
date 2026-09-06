from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.access_control.models import UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.files.services import (
    ProcessingFileSnapshot,
    get_processing_file_snapshot,
    get_survey_file_ids,
    get_survey_file_readiness,
    update_file_processing_state,
)
from apps.processing.models import ProcessingJob
from apps.projects.services import user_can_view_project_id
from apps.surveys.models import SurveyStatus
from apps.surveys.services import (
    SurveyWorkflowSnapshot,
    get_survey_workflow_snapshot,
    lock_survey_for_workflow,
    update_locked_survey_processing_state,
)

from .dispatch import register_processing_dispatch_on_commit
from .lease import ProcessingLeaseLost
from .types import MAX_AUTOMATIC_RETRIES, RETRY_DELAYS_MINUTES


@dataclass(frozen=True)
class ProcessingRun:
    job_id: int
    file_id: int
    retry_count: int
    file: ProcessingFileSnapshot

    @property
    def pk(self):
        return self.job_id


@dataclass(frozen=True)
class ProcessingJobReadiness:
    job_count: int
    all_jobs_completed: bool


@dataclass(frozen=True)
class ProcessingJobSummarySnapshot:
    id: int
    status: str
    progress_percent: int
    retry_count: int
    dispatch_status: str
    started_at: object
    completed_at: object
    created_at: object
    updated_at: object


@dataclass(frozen=True)
class ProcessingJobResponseSnapshot:
    summary: ProcessingJobSummarySnapshot
    file: ProcessingFileSnapshot


def get_processing_job_summaries_for_file_ids(*, file_ids):
    summaries = {}
    fields = [
        "id", "file_id", "status", "progress_percent", "retry_count", "dispatch_status",
        "started_at", "completed_at", "created_at", "updated_at",
    ]
    for row in ProcessingJob.objects.filter(file_id__in=file_ids).values(*fields):
        summaries[row["file_id"]] = ProcessingJobSummarySnapshot(
            id=row["id"],
            status=row["status"],
            progress_percent=row["progress_percent"],
            retry_count=row["retry_count"],
            dispatch_status=row["dispatch_status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    return summaries


def get_processing_job_for_file(*, file_id: int):
    return ProcessingJob.objects.get(file_id=file_id)


def get_survey_job_readiness(*, survey_id: int) -> ProcessingJobReadiness:
    file_ids = get_survey_file_ids(survey_id=survey_id)
    statuses = [summary.status for summary in get_processing_job_summaries_for_file_ids(file_ids=file_ids).values()]
    return ProcessingJobReadiness(
        job_count=len(statuses),
        all_jobs_completed=bool(statuses) and all(status == "completed" for status in statuses),
    )


def _load_job_context(*, processing_job_id: int, for_update: bool = False):
    queryset = ProcessingJob.objects
    if for_update:
        queryset = queryset.select_for_update()
    job = queryset.get(pk=processing_job_id)
    file_snapshot = get_processing_file_snapshot(file_id=job.file_id)
    survey_snapshot = (
        lock_survey_for_workflow(survey_id=file_snapshot.survey_id)
        if for_update
        else get_survey_workflow_snapshot(survey_id=file_snapshot.survey_id)
    )
    return job, file_snapshot, survey_snapshot


def _owns_running_lease(job, lease_token):
    return (
        job.status == "running" and job.lease_token == lease_token
        and job.lease_expires_at is not None and job.lease_expires_at > timezone.now()
    )


def schedule_processing_retry(*, processing_job_id: int, error_message: str, lease_token: str | None = None):
    with transaction.atomic():
        job, file_snapshot, survey_snapshot = _load_job_context(
            processing_job_id=processing_job_id,
            for_update=True,
        )
        if job.status == "completed" or (lease_token is not None and not _owns_running_lease(job, lease_token)):
            return None
        if job.retry_count >= MAX_AUTOMATIC_RETRIES:
            return None

        previous_retry_count = job.retry_count
        job.retry_count += 1
        delay_minutes = RETRY_DELAYS_MINUTES[job.retry_count - 1]
        job.status = "queued"
        job.dispatch_status = "dispatched"
        job.dispatch_available_at = timezone.now() + timedelta(minutes=delay_minutes)
        job.error_message = error_message
        job.completed_at = None
        job.lease_token = None
        job.lease_expires_at = None
        job.save(
            update_fields=[
                "retry_count", "status", "dispatch_status", "dispatch_available_at",
                "error_message", "completed_at", "lease_token", "lease_expires_at", "updated_at",
            ]
        )
        record_audit_event(
            action=AuditAction.PROCESSING_RETRY,
            entity_type="processing_job",
            entity_id=job.pk,
            project_id=survey_snapshot.project_id,
            survey_id=survey_snapshot.id,
            details={
                "retry_count": job.retry_count,
                "previous_retry_count": previous_retry_count,
                "automatic": True,
                "delay_minutes": delay_minutes,
            },
        )
        return delay_minutes * 60


def mark_processing_failed_permanently(*, processing_job_id: int, error_message: str, lease_token: str | None = None):
    with transaction.atomic():
        job, file_snapshot, survey_snapshot = _load_job_context(
            processing_job_id=processing_job_id,
            for_update=True,
        )
        if job.status == "completed" or (lease_token is not None and not _owns_running_lease(job, lease_token)):
            return False
        job.status = "failed"
        job.progress_percent = min(job.progress_percent, 99)
        job.completed_at = timezone.now()
        job.error_message = error_message
        job.lease_token = None
        job.lease_expires_at = None
        job.save(
            update_fields=[
                "status", "progress_percent", "completed_at", "error_message",
                "lease_token", "lease_expires_at", "updated_at",
            ]
        )
        update_file_processing_state(file_id=file_snapshot.id, status="failed")
        update_locked_survey_processing_state(
            survey_id=survey_snapshot.id,
            status=SurveyStatus.FAILED,
            processing_status="failed",
        )
        record_audit_event(
            action=AuditAction.PROCESSING_FAILED,
            entity_type="processing_job",
            entity_id=job.pk,
            project_id=survey_snapshot.project_id,
            survey_id=survey_snapshot.id,
            details={"retry_count": job.retry_count, "error": error_message},
        )
        return True


def manual_retry_processing_job(*, actor, processing_job_id: int):
    with transaction.atomic():
        job, file_snapshot, survey_snapshot = _load_job_context(
            processing_job_id=processing_job_id,
            for_update=True,
        )
        _validate_manual_retry_actor(actor=actor, survey=survey_snapshot)
        _validate_manual_retry_state(processing_job=job)

        previous_retry_count = job.retry_count
        job.retry_count = 0
        job.status = "queued"
        job.progress_percent = 0
        job.started_at = None
        job.completed_at = None
        job.error_message = None
        job.celery_task_id = None
        job.dispatch_status = "pending"
        job.dispatch_last_attempt_at = None
        job.dispatch_available_at = timezone.now()
        job.lease_token = None
        job.lease_expires_at = None
        job.last_heartbeat_at = None
        job.save(
            update_fields=[
                "retry_count", "status", "progress_percent", "started_at", "completed_at",
                "error_message", "celery_task_id", "dispatch_status", "dispatch_last_attempt_at",
                "dispatch_available_at", "lease_token", "lease_expires_at", "last_heartbeat_at", "updated_at",
            ]
        )
        update_file_processing_state(
            file_id=file_snapshot.id,
            status="uploading",
            preview_path=None,
            converted_path=None,
        )
        update_locked_survey_processing_state(
            survey_id=survey_snapshot.id,
            status=SurveyStatus.UPLOADING,
            processing_status="queued",
        )
        record_audit_event(
            action=AuditAction.PROCESSING_RETRY,
            entity_type="processing_job",
            entity_id=job.pk,
            user=actor,
            project_id=survey_snapshot.project_id,
            survey_id=survey_snapshot.id,
            details={
                "retry_count": job.retry_count,
                "previous_retry_count": previous_retry_count,
                "automatic": False,
                "starts_new_automatic_cycle": True,
            },
        )
        register_processing_dispatch_on_commit(processing_job_id=job.pk)
        return job


def get_processing_job_visible_to_user(*, actor, processing_job_id: int):
    job, file_snapshot, survey_snapshot = _load_job_context(processing_job_id=processing_job_id)
    if not user_can_view_project_id(user=actor, project_id=survey_snapshot.project_id):
        raise PermissionDenied("You do not have permission to access this processing job.")
    return job


def get_processing_job_for_response(*, processing_job_id: int):
    job = ProcessingJob.objects.get(pk=processing_job_id)
    return ProcessingJobResponseSnapshot(
        summary=get_processing_job_summaries_for_file_ids(file_ids=[job.file_id])[job.file_id],
        file=get_processing_file_snapshot(file_id=job.file_id),
    )


def _validate_manual_retry_actor(*, actor, survey: SurveyWorkflowSnapshot):
    if not actor.is_active:
        raise PermissionDenied("Only eligible active users can retry failed processing jobs.")
    if actor.role == UserRole.ADMINISTRATOR:
        return
    if actor.role == UserRole.SURVEY_ENGINEER and user_can_view_project_id(
        user=actor,
        project_id=survey.project_id,
    ):
        return
    raise PermissionDenied("Only eligible active users can retry failed processing jobs.")


def _validate_manual_retry_state(*, processing_job):
    if processing_job.status != "failed":
        raise ValidationError("Only permanently failed processing jobs can be retried manually.")


def _mark_job_running(*, processing_job_id: int, lease_token: str):
    with transaction.atomic():
        job, file_snapshot, survey_snapshot = _load_job_context(
            processing_job_id=processing_job_id,
            for_update=True,
        )
        now = timezone.now()
        if job.status != "queued":
            return None

        job.status = "running"
        job.progress_percent = 10
        job.started_at = job.started_at or now
        job.completed_at = None
        job.error_message = None
        job.lease_token = lease_token
        job.lease_expires_at = now + timedelta(seconds=settings.PROCESSING_RUNNING_LEASE_SECONDS)
        job.last_heartbeat_at = now
        job.save(
            update_fields=[
                "status", "progress_percent", "started_at", "completed_at", "error_message",
                "lease_token", "lease_expires_at", "last_heartbeat_at", "updated_at",
            ]
        )
        update_file_processing_state(file_id=file_snapshot.id, status="processing")
        update_locked_survey_processing_state(
            survey_id=survey_snapshot.id,
            status=SurveyStatus.PROCESSING,
            processing_status="processing",
        )
        record_audit_event(
            action=AuditAction.PROCESSING_STARTED,
            entity_type="processing_job",
            entity_id=job.pk,
            project_id=survey_snapshot.project_id,
            survey_id=survey_snapshot.id,
            details={"retry_count": job.retry_count},
        )
        return ProcessingRun(
            job_id=job.pk,
            file_id=file_snapshot.id,
            retry_count=job.retry_count,
            file=file_snapshot,
        )


def _set_job_progress(*, processing_job_id: int, progress_percent: int, lease_token: str | None = None):
    if lease_token is None:
        raise ProcessingLeaseLost("Processing worker ownership could not be verified.")

    queryset = ProcessingJob.objects.filter(pk=processing_job_id, status="running")
    queryset = queryset.filter(lease_token=lease_token, lease_expires_at__gt=timezone.now())
    try:
        updated = queryset.update(progress_percent=progress_percent, updated_at=timezone.now())
    except DatabaseError as exc:
        raise ProcessingLeaseLost("Processing worker ownership could not be verified.") from exc
    if updated != 1:
        raise ProcessingLeaseLost("Processing worker ownership was lost.")


def _mark_job_completed(*, processing_job_id: int, lease_token: str | None = None, preview_path: str | None, converted_path: str | None):
    if lease_token is None:
        return False

    with transaction.atomic():
        job, file_snapshot, survey_snapshot = _load_job_context(
            processing_job_id=processing_job_id,
            for_update=True,
        )
        if job.status != "running" or not _owns_running_lease(job, lease_token):
            return False

        completed_at = timezone.now()
        updated = ProcessingJob.objects.filter(
            pk=job.pk,
            status="running",
            lease_token=lease_token,
            lease_expires_at__gt=completed_at,
        ).update(
            status="completed",
            progress_percent=100,
            completed_at=completed_at,
            error_message=None,
            lease_token=None,
            lease_expires_at=None,
            updated_at=completed_at,
        )
        if updated != 1:
            return False

        update_file_processing_state(
            file_id=file_snapshot.id,
            status="ready",
            preview_path=preview_path,
            converted_path=converted_path,
        )

        file_readiness = get_survey_file_readiness(survey_id=survey_snapshot.id)
        job_readiness = get_survey_job_readiness(survey_id=survey_snapshot.id)
        next_status = SurveyStatus.READY if file_readiness.all_files_ready and job_readiness.all_jobs_completed else SurveyStatus.PROCESSING
        next_processing_status = "completed" if next_status == SurveyStatus.READY else "processing"
        update_locked_survey_processing_state(
            survey_id=survey_snapshot.id,
            status=next_status,
            processing_status=next_processing_status,
        )
        record_audit_event(
            action=AuditAction.PROCESSING_COMPLETED,
            entity_type="processing_job",
            entity_id=job.pk,
            project_id=survey_snapshot.project_id,
            survey_id=survey_snapshot.id,
            details={"retry_count": job.retry_count},
        )
        return True
