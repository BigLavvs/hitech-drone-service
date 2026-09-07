import hashlib
import base64
import struct
import zlib
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
from apps.files.validation.rules import VALIDATION_READ_CHUNK_BYTES
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


def valid_png_bytes(*, interlaced=False, scan_data=None):
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
        )

    if scan_data is None:
        scan_data = b"\x00\xff\x00\x00\xff"
    return signature + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, int(interlaced))) + chunk(b"IDAT", zlib.compress(scan_data)) + chunk(b"IEND", b"")


def valid_jpeg_bytes():
    # Reuse the independently verified JPEG fixture rather than the warning-tainted sample.
    return valid_progressive_jpeg_bytes()


def valid_progressive_jpeg_bytes():
    # A browser-decodable progressive JPEG fixture.
    encoded = (
        "/9j/4QAYRXhpZgAASUkqAAgAAAAAAAAAAAAAAP/sABFEdWNreQABAAQAAABaAAD/4QMxaHR0cDovL25zLmFkb2JlLmNvbS94YXAvMS4wLwA8P3hwYWNrZXQg"
        "YmVnaW49Iu+7vyIgaWQ9Ilc1TTBNcENlaGlIenJlU3pOVGN6a2M5ZCI/PiA8eDp4bXBtZXRhIHhtbG5zOng9ImFkb2JlOm5zOm1ldGEvIiB4OnhtcHRrPSJB"
        "ZG9iZSBYTVAgQ29yZSA1LjUtYzAyMSA3OS4xNTU3NzIsIDIwMTQvMDEvMTMtMTk6NDQ6MDAgICAgICAgICI+IDxyZGY6UkRGIHhtbG5zOnJkZj0iaHR0cDov"
        "L3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyI+IDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiIHhtbG5zOnhtcD0iaHR0cDovL25z"
        "LmFkb2JlLmNvbS94YXAvMS4wLyIgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iIHhtbG5zOnN0UmVmPSJodHRwOi8vbnMu"
        "YWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VSZWYjIiB4bXA6Q3JlYXRvclRvb2w9IkFkb2JlIFBob3Rvc2hvcCBDQyAyMDE0IChNYWNpbnRvc2gp"
        "IiB4bXBNTTpJbnN0YW5jZUlEPSJ4bXAuaWlkOkIyQjg2RTdEOTBCMTExRTRCOUQ0ODU3RUUyNUI3NDIyIiB4bXBNTTpEb2N1bWVudElEPSJ4bXAuZGlkOkIy"
        "Qjg2RTdFOTBCMTExRTRCOUQ0ODU3RUUyNUI3NDIyIj4gPHhtcE1NOkRlcml2ZWRGcm9tIHN0UmVmOmluc3RhbmNlSUQ9InhtcC5paWQ6QjJCODZFN0I5MEIx"
        "MTFFNEI5RDQ4NTdFRTI1Qjc0MjIiIHN0UmVmOmRvY3VtZW50SUQ9InhtcC5kaWQ6QjJCODZFN0M5MEIxMTFFNEI5RDQ4NTdFRTI1Qjc0MjIiLz4gPC9yZGY6"
        "RGVzY3JpcHRpb24+IDwvcmRmOlJERj4gPC94OnhtcG1ldGE+IDw/eHBhY2tldCBlbmQ9InIiPz7/7gAmQWRvYmUAZMAAAAABAwAVBAMGCg0AABhUAAAubwAA"
        "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"
    )
    return base64.b64decode(encoded)

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
