import uuid

from celery import shared_task

from apps.processing.services import (
    execute_processing_task,
    mark_processing_failed_permanently,
    reconcile_expired_running_processing_jobs,
    reconcile_stale_queued_processing_jobs,
    schedule_processing_retry,
)
from apps.processing.services.lease import ProcessingLeaseLost


@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
    name="apps.processing.process_2d_file",
)
def process_2d_file(self, processing_job_id: int):
    return _run_processing_task(task=self, processing_job_id=processing_job_id)


@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
    name="apps.processing.process_3d_file",
)
def process_3d_file(self, processing_job_id: int):
    return _run_processing_task(task=self, processing_job_id=processing_job_id)


def _run_processing_task(*, task, processing_job_id: int):
    # Celery can redeliver/retry the same task ID. Ownership identifies an
    # execution attempt, never the broker message shared by two workers.
    lease_token = uuid.uuid4().hex
    try:
        return execute_processing_task(processing_job_id=processing_job_id, lease_token=lease_token)
    except ProcessingLeaseLost:
        # Lease reconciliation owns recovery. Do not retry or mutate a job
        # after ownership could not be verified by the worker.
        return {"status": "ignored"}
    except Exception as exc:
        countdown = schedule_processing_retry(
            processing_job_id=processing_job_id,
            error_message=str(exc),
            lease_token=lease_token,
        )
        if countdown is None:
            failed = mark_processing_failed_permanently(
                processing_job_id=processing_job_id,
                error_message=str(exc),
                lease_token=lease_token,
            )
            return {"status": "failed" if failed else "ignored"}
        raise task.retry(exc=exc, countdown=countdown)


@shared_task(name="apps.processing.reconcile_stale_queued_processing_jobs")
def reconcile_stale_queued_processing_jobs_task():
    return reconcile_stale_queued_processing_jobs()


@shared_task(name="apps.processing.reconcile_expired_running_processing_jobs")
def reconcile_expired_running_processing_jobs_task():
    return reconcile_expired_running_processing_jobs()
