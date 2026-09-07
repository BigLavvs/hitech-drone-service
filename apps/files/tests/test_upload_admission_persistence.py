from .support import *


class UploadAdmissionPersistenceMixin:
    @patch("apps.files.services.validate_upload", side_effect=FileValidationError("invalid upload"))
    def test_validation_happens_before_r2_upload(self, mocked_validate_upload):
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(FileValidationError, "invalid upload"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        mocked_validate_upload.assert_called_once()
        self.assertEqual(storage.uploaded, [])

    def test_sha256_is_stored_for_new_upload(self):
        storage = FakePrivateStorageAdapter()
        upload_content = valid_png_bytes()
        upload = self.make_upload(content=upload_content, name="preview.png", content_type="image/png")

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=upload,
            storage=storage,
        )

        self.assertEqual(
            result.survey_file.sha256_checksum,
            hashlib.sha256(upload_content).hexdigest(),
        )

    @override_settings(MAX_SURVEY_TOTAL_SIZE_BYTES=40)
    def test_survey_total_size_limit_is_enforced(self):
        self.create_existing_file_with_job(
            size_bytes=30,
            sha256_checksum="b" * 64,
            storage_path="surveys/1/files/1/raw.tif",
        )
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(
            ValidationError,
            "Survey exceeds the configured total upload size limit.",
        ):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(
                    content=b"II*\x00\x08\x00\x00\x00\x00\x00more-bytes",
                    name="image.tiff",
                ),
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    def test_duplicate_checksum_returns_existing_file_and_job_and_cleans_staging(self, mocked_dispatch):
        existing_upload = self.make_upload()
        existing_checksum = hashlib.sha256(existing_upload.getvalue()).hexdigest()
        existing_file, existing_job = self.create_existing_file_with_job(
            sha256_checksum=existing_checksum,
            storage_path="surveys/1/files/55/raw.tif",
        )
        self.survey.status = SurveyStatus.READY
        self.survey.processing_status = "completed"
        self.survey.save(update_fields=["status", "processing_status", "updated_at"])
        storage = FakePrivateStorageAdapter()

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=self.make_upload(),
            storage=storage,
        )

        self.survey.refresh_from_db()
        self.assertFalse(result.created)
        self.assertEqual(result.survey_file.pk, existing_file.pk)
        self.assertEqual(result.processing_job.pk, existing_job.pk)
        self.assertEqual(SurveyFile.objects.count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(self.survey.status, SurveyStatus.READY)
        self.assertEqual(self.survey.processing_status, "completed")
        self.assertEqual(len(storage.uploaded), 1)
        self.assertEqual(storage.deleted, storage.uploaded)
        self.assertEqual(storage.promoted, [])
        mocked_dispatch.assert_not_called()

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    def test_successful_new_admission_creates_file_job_audit_and_updates_survey(self, mocked_dispatch):
        storage = FakePrivateStorageAdapter()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = admit_uploaded_file(
                actor=self.assigned_engineer,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.survey.refresh_from_db()
        survey_file = SurveyFile.objects.get()
        processing_job = ProcessingJob.objects.get()
        audit_log = AuditLog.objects.get()

        self.assertTrue(result.created)
        self.assertEqual(result.survey_file.pk, survey_file.pk)
        self.assertEqual(result.processing_job.pk, processing_job.pk)
        self.assertEqual(processing_job.file_id, survey_file.pk)
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(audit_log.action, AuditAction.FILE_UPLOADED)
        self.assertEqual(audit_log.entity_type, "survey_file")
        self.assertEqual(audit_log.entity_id, survey_file.pk)
        self.assertEqual(survey_file.storage_path, f"surveys/{self.survey.pk}/files/{survey_file.pk}/raw.tif")
        self.assertTrue(survey_file.storage_path.endswith("/raw.tif"))
        self.assertEqual(self.survey.status, SurveyStatus.UPLOADING)
        self.assertEqual(self.survey.processing_status, "queued")
        self.assertEqual(len(storage.promoted), 1)
        self.assertEqual(storage.deleted, storage.uploaded)
        self.assertEqual(len(callbacks), 1)
        mocked_dispatch.assert_not_called()

        callbacks[0]()
        mocked_dispatch.assert_called_once_with(processing_job_id=processing_job.pk)

    @patch("apps.processing.services.dispatch.logger")
    @patch("apps.processing.services.dispatch.dispatch_processing_job", side_effect=RuntimeError("broker unavailable"))
    def test_broker_failure_leaves_accepted_job_queued_and_logs_safely(self, mocked_dispatch, mocked_logger):
        storage = FakePrivateStorageAdapter()

        with self.captureOnCommitCallbacks(execute=True):
            result = admit_uploaded_file(
                actor=self.assigned_engineer,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        processing_job = ProcessingJob.objects.get(pk=result.processing_job.pk)
        self.assertEqual(processing_job.status, "queued")
        self.assertIsNone(processing_job.celery_task_id)
        mocked_dispatch.assert_called_once_with(processing_job_id=processing_job.pk)
        mocked_logger.warning.assert_called_once()

    @patch(
        "apps.processing.services.dispatch._mark_dispatch_failed",
        side_effect=RuntimeError("dispatch status database unavailable"),
    )
    @patch(
        "apps.processing.services.dispatch.dispatch_processing_job",
        side_effect=RuntimeError("broker unavailable"),
    )
    def test_post_commit_dispatch_status_failure_preserves_canonical_objects_and_rows(
        self, mocked_dispatch, mocked_mark_failed
    ):
        storage = FakePrivateStorageAdapter()

        with self.captureOnCommitCallbacks(execute=True):
            result = admit_uploaded_file(
                actor=self.assigned_engineer,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        survey_file = SurveyFile.objects.get(pk=result.survey_file.pk)
        processing_job = ProcessingJob.objects.get(pk=result.processing_job.pk)
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(survey_file.storage_path, storage.promoted[0][1])
        self.assertIn(survey_file.storage_path, storage.objects)
        self.assertEqual(len(storage.uploaded), 1)
        self.assertEqual(mocked_dispatch.call_count, 1)
        mocked_mark_failed.assert_called_once_with(processing_job_id=processing_job.pk)

    @patch("apps.files.services.record_audit_event", side_effect=RuntimeError("audit write failed"))
    def test_audit_failure_rolls_back_records_and_cleans_storage(self, mocked_record_audit_event):
        storage = FakePrivateStorageAdapter()

        with self.assertRaisesMessage(RuntimeError, "audit write failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(len(storage.uploaded), 1)
        self.assertGreaterEqual(len(storage.deleted), 2)
        self.assertEqual(storage.objects, {})
        mocked_record_audit_event.assert_called_once()

    def test_promotion_failure_rolls_back_records_and_cleans_storage(self):
        storage = FakePrivateStorageAdapter()

        def fail_promotion(*, source_key, destination_key, content_type):
            storage.objects[destination_key] = {"content": b"copied", "content_type": content_type}
            raise RuntimeError("promotion failed")

        storage.promote_object = fail_promotion

        with self.assertRaisesMessage(RuntimeError, "promotion failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=self.make_upload(),
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertEqual(storage.objects, {})
