import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.processing.models import ProcessingJob
from apps.surveys.models import SurveyStatus

from .types import DispatchResult, MAX_AUTOMATIC_RETRIES

logger = logging.getLogger(__name__)


def create_queued_processing_job(*, survey_file=None, file_id: int | None = None):
    resolved_file_id = file_id if file_id is not None else survey_file.pk
    return ProcessingJob.objects.create(file_id=resolved_file_id, status="queued")


def register_processing_dispatch_on_commit(*, processing_job_id: int):
    transaction.on_commit(lambda: dispatch_processing_job_safely(processing_job_id=processing_job_id))


def dispatch_processing_job_safely(*, processing_job_id: int) -> DispatchResult:
    try:
        return dispatch_processing_job(processing_job_id=processing_job_id)
    except Exception:
        _mark_dispatch_failed(processing_job_id=processing_job_id)
        logger.warning(
            "Processing dispatch unavailable; upload remains queued.",
            extra={"processing_job_id": processing_job_id},
        )
        return DispatchResult(dispatched=False, celery_task_id=None)


def dispatch_processing_job(*, processing_job_id: int) -> DispatchResult:
    from apps.files.services import get_processing_file_snapshot

    processing_job = ProcessingJob.objects.only(
        "id", "file_id", "status", "celery_task_id"
    ).get(pk=processing_job_id)
    if processing_job.status != "queued":
        return DispatchResult(dispatched=False, celery_task_id=processing_job.celery_task_id)

    file_snapshot = get_processing_file_snapshot(file_id=processing_job.file_id)
    if file_snapshot.file_type == "TWO_D":
        from apps.processing.tasks import process_2d_file

        async_result = process_2d_file.apply_async(args=[processing_job.pk])
    else:
        from apps.processing.tasks import process_3d_file

        async_result = process_3d_file.apply_async(args=[processing_job.pk])

    now = timezone.now()
    ProcessingJob.objects.filter(pk=processing_job.pk).update(
        celery_task_id=async_result.id,
        dispatch_status="dispatched",
        dispatch_last_attempt_at=now,
        dispatch_available_at=now,
        updated_at=now,
    )
    return DispatchResult(dispatched=True, celery_task_id=async_result.id)


def _mark_dispatch_failed(*, processing_job_id: int):
    now = timezone.now()
    ProcessingJob.objects.filter(pk=processing_job_id, status="queued").update(
        dispatch_status="failed",
        dispatch_last_attempt_at=now,
        dispatch_available_at=now,
        dispatch_failure_count=F("dispatch_failure_count") + 1,
        updated_at=now,
    )


def reconcile_stale_queued_processing_jobs() -> dict:
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.PROCESSING_QUEUED_DISPATCH_STALE_AFTER_SECONDS)
    claimed_jobs = []

    with transaction.atomic():
        candidates = (
            ProcessingJob.objects.select_for_update(skip_locked=True)
            .filter(status="queued")
            .filter(
                Q(dispatch_available_at__isnull=True, updated_at__lte=cutoff)
                | Q(dispatch_available_at__lte=cutoff)
            )
            .order_by("updated_at", "pk")[: settings.PROCESSING_RECONCILE_BATCH_SIZE]
        )
        for processing_job in candidates:
            from apps.surveys.services import get_survey_workflow_snapshot
            from apps.files.services import get_processing_file_snapshot

            file_snapshot = get_processing_file_snapshot(file_id=processing_job.file_id)
            survey_snapshot = get_survey_workflow_snapshot(survey_id=file_snapshot.survey_id)
            processing_job.dispatch_status = "recovering"
            processing_job.dispatch_last_attempt_at = now
            processing_job.dispatch_available_at = now
            processing_job.save(
                update_fields=[
                    "dispatch_status", "dispatch_last_attempt_at", "dispatch_available_at", "updated_at"
                ]
            )
            record_audit_event(
                action=AuditAction.PROCESSING_RECOVERY,
                entity_type="processing_job",
                entity_id=processing_job.pk,
                project_id=survey_snapshot.project_id,
                survey_id=survey_snapshot.id,
                details={
                    "recovery_type": "queued_dispatch",
                    "automatic": True,
                    "reason": "stale_queued_job",
                    "retry_count": processing_job.retry_count,
                },
            )
            claimed_jobs.append(processing_job.pk)

    return _dispatch_claimed_jobs(claimed_jobs)


def reconcile_expired_running_processing_jobs() -> dict:
    now = timezone.now()
    recovered_job_ids = []
    failed_permanently = 0

    with transaction.atomic():
        candidates = (
            ProcessingJob.objects.select_for_update(skip_locked=True)
            .filter(status="running")
            .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=now))
            .order_by("lease_expires_at", "pk")[: settings.PROCESSING_RECONCILE_BATCH_SIZE]
        )
        for processing_job in candidates:
            from apps.files.services import get_processing_file_snapshot, update_file_processing_state
            from apps.surveys.services import (
                lock_survey_for_workflow,
                update_locked_survey_processing_state,
            )

            file_snapshot = get_processing_file_snapshot(file_id=processing_job.file_id)
            survey_snapshot = lock_survey_for_workflow(survey_id=file_snapshot.survey_id)
            if processing_job.retry_count >= MAX_AUTOMATIC_RETRIES:
                processing_job.status = "failed"
                processing_job.progress_percent = min(processing_job.progress_percent, 99)
                processing_job.completed_at = now
                processing_job.error_message = "Processing worker lease expired."
                processing_job.lease_token = None
                processing_job.lease_expires_at = None
                processing_job.last_heartbeat_at = None
                processing_job.dispatch_status = "failed"
                processing_job.save(update_fields=[
                    "status", "progress_percent", "completed_at", "error_message",
                    "lease_token", "lease_expires_at", "last_heartbeat_at",
                    "dispatch_status", "updated_at",
                ])
                update_file_processing_state(file_id=file_snapshot.id, status="failed")
                update_locked_survey_processing_state(
                    survey_id=survey_snapshot.id,
                    status=SurveyStatus.FAILED,
                    processing_status="failed",
                )
                record_audit_event(
                    action=AuditAction.PROCESSING_FAILED,
                    entity_type="processing_job",
                    entity_id=processing_job.pk,
                    project_id=survey_snapshot.project_id,
                    survey_id=survey_snapshot.id,
                    details={
                        "retry_count": processing_job.retry_count,
                        "error": "Processing worker lease expired.",
                        "recovery": True,
                    },
                )
                failed_permanently += 1
                continue

            previous_retry_count = processing_job.retry_count
            processing_job.retry_count += 1
            processing_job.status = "queued"
            processing_job.dispatch_status = "pending"
            processing_job.dispatch_last_attempt_at = None
            processing_job.dispatch_available_at = now
            processing_job.error_message = "Processing worker lease expired."
            processing_job.lease_token = None
            processing_job.lease_expires_at = None
            processing_job.last_heartbeat_at = None
            processing_job.save(update_fields=[
                "retry_count", "status", "dispatch_status", "dispatch_last_attempt_at",
                "dispatch_available_at", "error_message", "lease_token", "lease_expires_at",
                "last_heartbeat_at", "updated_at",
            ])
            record_audit_event(
                action=AuditAction.PROCESSING_RECOVERY,
                entity_type="processing_job",
                entity_id=processing_job.pk,
                project_id=survey_snapshot.project_id,
                survey_id=survey_snapshot.id,
                details={
                    "recovery_type": "expired_running_lease",
                    "automatic": True,
                    "retry_count": processing_job.retry_count,
                    "previous_retry_count": previous_retry_count,
                    "delay_minutes": 0,
                },
            )
            recovered_job_ids.append(processing_job.pk)

    result = _dispatch_claimed_jobs(recovered_job_ids)
    return {
        "recovered": len(recovered_job_ids),
        "dispatched": result["dispatched"],
        "failed_dispatches": result["failed_dispatches"],
        "failed_permanently": failed_permanently,
    }


def _dispatch_claimed_jobs(job_ids: list[int]) -> dict:
    dispatched = 0
    failed_dispatches = 0
    for processing_job_id in job_ids:
        result = dispatch_processing_job_safely(processing_job_id=processing_job_id)
        if result.dispatched:
            dispatched += 1
        else:
            failed_dispatches += 1
    return {"claimed": len(job_ids), "dispatched": dispatched, "failed_dispatches": failed_dispatches}
