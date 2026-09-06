"""Worker ownership checks and attempt-isolated generated storage."""

import threading
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone

from apps.processing.models import ProcessingJob
from .types import ProcessingError


class ProcessingLeaseLost(ProcessingError):
    """This worker may no longer publish or change processing state."""


def refresh_processing_lease(*, processing_job_id, lease_token):
    now = timezone.now()
    return ProcessingJob.objects.filter(
        pk=processing_job_id, status="running", lease_token=lease_token,
        lease_expires_at__gt=now,
    ).update(
        last_heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=settings.PROCESSING_RUNNING_LEASE_SECONDS),
        updated_at=now,
    ) == 1


class ProcessingHeartbeat:
    def __init__(self, *, processing_job_id, lease_token):
        self.processing_job_id = processing_job_id
        self.lease_token = lease_token
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _renew(self):
        try:
            renewed = refresh_processing_lease(
                processing_job_id=self.processing_job_id, lease_token=self.lease_token,
            )
        except Exception as exc:
            self._lost.set()
            raise ProcessingLeaseLost("Processing worker ownership could not be verified.") from exc
        if not renewed:
            self._lost.set()
            raise ProcessingLeaseLost("Processing worker ownership was lost.")

    def check(self):
        if self._lost.is_set():
            raise ProcessingLeaseLost("Processing worker ownership was lost.")
        self._renew()

    def __enter__(self):
        self.check()
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join(timeout=2)
        return False

    def _run(self):
        # Django connections belong to threads; the heartbeat owns its connection.
        try:
            try:
                close_old_connections()
            except Exception:
                self._lost.set()
                return
            while not self._stop.wait(settings.PROCESSING_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    close_old_connections()
                    self._renew()
                except ProcessingLeaseLost:
                    return
                except Exception:
                    self._lost.set()
                    return
        finally:
            connections.close_all()


class AttemptStorage:
    """Keep even in-flight uploads from touching another attempt's objects.

    Generation code uses the existing logical keys. Only this adapter translates
    writes to a unique private prefix; publication happens in the database after
    a final ownership check. Abandoned attempts are never exposed by readers.
    """

    def __init__(self, *, storage, heartbeat, survey_id, file_id):
        self.storage = storage
        self.heartbeat = heartbeat
        self.root = f"surveys/{survey_id}/files/{file_id}/"
        self.prefix = f"{self.root}attempts/{uuid.uuid4().hex}/"

    def published_path(self, logical_key):
        if logical_key is None:
            return None
        if not logical_key.startswith(self.root):
            raise ProcessingError("Generated file path is outside the processing file.")
        relative = logical_key[len(self.root):]
        if not relative or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise ProcessingError("Generated file path is invalid.")
        return self.prefix + relative

    def download_to_fileobj(self, *, storage_key, file_obj):
        self.heartbeat.check()
        self.storage.download_to_fileobj(storage_key=storage_key, file_obj=file_obj)
        self.heartbeat.check()

    def upload_generated_fileobj(self, *, destination_key, file_obj, content_type):
        self.heartbeat.check()
        self.storage.upload_generated_fileobj(
            destination_key=self.published_path(destination_key),
            file_obj=file_obj, content_type=content_type,
        )
        self.heartbeat.check()
