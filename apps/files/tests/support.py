import hashlib
import struct
from datetime import datetime, timedelta, timezone
from datetime import date
from io import BytesIO
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.files.models import FileFormat, FileType, SurveyFile, SurveyFileAsset, UploadSession
from apps.files.services import admit_uploaded_file, get_survey_file_download_for_user
from apps.files.storage import PrivateR2StorageAdapter
from apps.files.validation import (
    MAX_VALIDATION_BYTES,
    FileValidationError,
    sanitize_storage_filename,
    validate_obj_asset_upload,
    validate_upload,
)
from apps.processing.models import ProcessingJob
from apps.projects.models import Project, ProjectMembership, Site
from apps.surveys.models import Survey, SurveyStatus


FAKE_R2_SETTINGS = {
    "R2_ENDPOINT_URL": "https://example.invalid",
    "R2_ACCESS_KEY_ID": "test-access-key",
    "R2_SECRET_ACCESS_KEY": "test-secret-key",
    "R2_BUCKET_NAME": "test-bucket",
    "R2_PUBLIC_URL": "",
    "UPLOAD_CHUNK_SIZE_BYTES": 8 * 1024 * 1024,
}

class TrackingUpload(BytesIO):
    def __init__(self, name, content, content_type, fail_above=None):
        super().__init__(content)
        self.name = name
        self.content_type = content_type
        self.size = len(content)
        self.fail_above = fail_above
        self.max_requested_read = 0
        self.read_calls = []

    def read(self, size=-1):
        self.read_calls.append((self.tell(), size))
        if size > self.max_requested_read:
            self.max_requested_read = size
        if self.fail_above is not None and size > self.fail_above:
            raise AssertionError(f"Attempted to read beyond bounded prefix: {size}")
        return super().read(size)

class FakePrivateStorageAdapter:
    def __init__(self):
        self.objects = {}
        self.uploaded = []
        self.promoted = []
        self.deleted = []

    def build_staging_key(self, survey_id, filename, identifier=None):
        suffix = identifier or "stage"
        sanitized_filename = sanitize_storage_filename(filename)
        return f"surveys/{survey_id}/staging/{suffix}_{sanitized_filename}"

    def build_canonical_key(self, survey_id, file_id, extension):
        normalized_extension = extension.lower().lstrip(".")
        return f"surveys/{survey_id}/files/{file_id}/raw.{normalized_extension}"

    def upload_to_staging(self, *, survey_id, filename, file_obj, content_type, identifier=None):
        file_obj.seek(0)
        digest = hashlib.sha256()
        content = bytearray()

        while True:
            chunk = file_obj.read(4)
            if not chunk:
                break
            digest.update(chunk)
            content.extend(chunk)

        storage_key = self.build_staging_key(
            survey_id=survey_id,
            filename=filename,
            identifier=identifier,
        )
        self.objects[storage_key] = {
            "content": bytes(content),
            "content_type": content_type,
        }
        self.uploaded.append(storage_key)
        return type(
            "StagedUpload",
            (),
            {
                "storage_key": storage_key,
                "sha256_checksum": digest.hexdigest(),
            },
        )()

    def promote_object(self, *, source_key, destination_key, content_type):
        source_object = self.objects[source_key]
        self.objects[destination_key] = {
            "content": source_object["content"],
            "content_type": content_type,
        }
        self.promoted.append((source_key, destination_key))
        self.delete_object(source_key)

    def delete_object(self, storage_key):
        self.deleted.append(storage_key)
        self.objects.pop(storage_key, None)

__all__ = [name for name in globals() if not name.startswith('__')]
