import json
import tempfile
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileFormat, FileType, SurveyFile, SurveyFileAsset
from apps.processing.models import ProcessingJob
from apps.processing.services import (
    ASSESSMENT_MAX_GENERATED_TILE_ZOOM,
    MAX_AUTOMATIC_RETRIES,
    POTREE_METADATA_FILENAME,
    RETRY_DELAYS_MINUTES,
    _derive_max_zoom,
    _generate_xyz_tile_pyramid,
    ProcessingError,
    create_queued_processing_job,
    dispatch_processing_job,
    dispatch_processing_job_safely,
    execute_processing_task,
    manual_retry_processing_job,
    reconcile_expired_running_processing_jobs,
    reconcile_stale_queued_processing_jobs,
)
from apps.processing.tasks import _run_processing_task
from apps.projects.models import Project, ProjectMembership, Site
from apps.surveys.models import Survey, SurveyStatus

class FakePrivateStorageAdapter:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.download_calls = []
        self.upload_calls = []

    def download_to_fileobj(self, *, storage_key, file_obj):
        self.download_calls.append(storage_key)
        payload = self.objects[storage_key]
        for index in range(0, len(payload), 3):
            file_obj.write(payload[index : index + 3])

    def upload_generated_fileobj(self, *, destination_key, file_obj, content_type):
        self.upload_calls.append((destination_key, content_type))
        file_obj.seek(0)
        self.objects[destination_key] = file_obj.read()

class FakeMaskedArray:
    def __init__(self, bands):
        self._bands = bands

    def filled(self, _value):
        return self._bands


def make_fake_task(task_id="processing-task-1"):
    fake_task = Mock()
    fake_task.request.id = task_id
    return fake_task


def mark_job_running_with_lease(processing_job, lease_token):
    now = timezone.now()
    ProcessingJob.objects.filter(pk=processing_job.pk).update(
        status="running",
        lease_token=lease_token,
        lease_expires_at=now + timedelta(minutes=10),
        last_heartbeat_at=now,
    )
    processing_job.refresh_from_db()


def representative_tile_metadata():
    return {
        "bounds": [3.0, 6.0, 4.0, 7.0],
        "zoom_range": {"min": 0, "max": 12},
        "tile_matrix_bounds": {"12": {"x_min": 1, "x_max": 2, "y_min": 3, "y_max": 4}},
        "generated_tile_count": 8,
    }

__all__ = [name for name in globals() if not name.startswith('__')]
