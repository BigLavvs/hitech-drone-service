import hashlib
from dataclasses import dataclass
from uuid import uuid4

import boto3
from boto3.s3.transfer import TransferConfig
from django.conf import settings

from apps.files.validation import sanitize_storage_filename


@dataclass(frozen=True)
class StagedUpload:
    storage_key: str
    sha256_checksum: str


class _HashingReader:
    def __init__(self, file_obj):
        self._file_obj = file_obj
        self._hasher = hashlib.sha256()

    @property
    def sha256_checksum(self):
        return self._hasher.hexdigest()

    def read(self, size=-1):
        chunk = self._file_obj.read(size)
        if chunk:
            self._hasher.update(chunk)
        return chunk

    def __getattr__(self, name):
        return getattr(self._file_obj, name)


class PrivateR2StorageAdapter:
    def __init__(self, client=None, bucket_name=None):
        self.bucket_name = bucket_name or settings.R2_BUCKET_NAME
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )

    def _build_transfer_config(self):
        return TransferConfig(
            multipart_threshold=settings.UPLOAD_CHUNK_SIZE_BYTES,
            multipart_chunksize=settings.UPLOAD_CHUNK_SIZE_BYTES,
        )

    def build_staging_key(self, survey_id, filename, identifier=None):
        sanitized_filename = sanitize_storage_filename(filename)
        collision_id = identifier or uuid4().hex
        return f"surveys/{survey_id}/staging/{collision_id}_{sanitized_filename}"

    def build_canonical_key(self, survey_id, file_id, extension):
        normalized_extension = extension.lower().lstrip(".")
        return f"surveys/{survey_id}/files/{file_id}/raw.{normalized_extension}"

    def build_storage_key(self, survey_id, filename, identifier=None):
        return self.build_staging_key(
            survey_id=survey_id,
            filename=filename,
            identifier=identifier,
        )

    def upload_to_staging(self, *, survey_id, filename, file_obj, content_type, identifier=None):
        storage_key = self.build_staging_key(
            survey_id=survey_id,
            filename=filename,
            identifier=identifier,
        )
        file_obj.seek(0)
        hashing_reader = _HashingReader(file_obj)
        self.client.upload_fileobj(
            Fileobj=hashing_reader,
            Bucket=self.bucket_name,
            Key=storage_key,
            ExtraArgs={"ContentType": content_type},
            Config=self._build_transfer_config(),
        )
        return StagedUpload(
            storage_key=storage_key,
            sha256_checksum=hashing_reader.sha256_checksum,
        )

    def upload(self, *, survey_id, filename, file_obj, content_type, identifier=None):
        return self.upload_to_staging(
            survey_id=survey_id,
            filename=filename,
            file_obj=file_obj,
            content_type=content_type,
            identifier=identifier,
        ).storage_key

    def promote_object(self, *, source_key, destination_key, content_type):
        copy_source = {"Bucket": self.bucket_name, "Key": source_key}
        self.client.copy_object(
            Bucket=self.bucket_name,
            Key=destination_key,
            CopySource=copy_source,
            ContentType=content_type,
            MetadataDirective="REPLACE",
        )
        self.delete_object(source_key)

    def delete_object(self, storage_key):
        self.client.delete_object(Bucket=self.bucket_name, Key=storage_key)

    def download_to_fileobj(self, *, storage_key, file_obj):
        self.client.download_fileobj(
            Bucket=self.bucket_name,
            Key=storage_key,
            Fileobj=file_obj,
            Config=self._build_transfer_config(),
        )

    def upload_generated_fileobj(self, *, destination_key, file_obj, content_type):
        file_obj.seek(0)
        self.client.upload_fileobj(
            Fileobj=file_obj,
            Bucket=self.bucket_name,
            Key=destination_key,
            ExtraArgs={"ContentType": content_type},
            Config=self._build_transfer_config(),
        )

    def generate_private_download_url(self, *, storage_key, expires_in=300):
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": storage_key,
            },
            ExpiresIn=expires_in,
        )
