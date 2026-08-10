from celery import shared_task

from apps.processing.services import (
    execute_processing_task,
    mark_processing_failed_permanently,
    schedule_processing_retry,
)


@shared_task(bind=True, max_retries=3, name="apps.processing.process_2d_file")
def process_2d_file(self, processing_job_id: int):
    return _run_processing_task(task=self, processing_job_id=processing_job_id)


@shared_task(bind=True, max_retries=3, name="apps.processing.process_3d_file")
def process_3d_file(self, processing_job_id: int):
    return _run_processing_task(task=self, processing_job_id=processing_job_id)


def _run_processing_task(*, task, processing_job_id: int):
    try:
        return execute_processing_task(processing_job_id=processing_job_id)
    except Exception as exc:
        countdown = schedule_processing_retry(
            processing_job_id=processing_job_id,
            error_message=str(exc),
        )
        if countdown is None:
            mark_processing_failed_permanently(
                processing_job_id=processing_job_id,
                error_message=str(exc),
            )
            return {"status": "failed"}
        raise task.retry(exc=exc, countdown=countdown)
