from .support import *


class ProcessingRetryMixin:
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_checksum_mismatch_schedules_retry(self, storage_factory):
        survey_file, processing_job, _raw_bytes = self.create_file_and_job()
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: b"corrupt"})
        fake_task = make_fake_task("checksum-retry-task")
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(processing_job.retry_count, 1)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(),
            1,
        )
        fake_task.retry.assert_called_once()

    @patch("apps.processing.tasks.execute_processing_task", side_effect=ProcessingError("File processing failed."))
    def test_automatic_retry_schedule_uses_assessment_backoff(self, _mocked_execute):
        for starting_retry_count, expected_countdown in enumerate((120, 300, 600)):
            with self.subTest(starting_retry_count=starting_retry_count):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Retry Survey {starting_retry_count}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename="source.tif",
                    stored_filename="source.tif",
                    file_type=FileType.TWO_D,
                    format=FileFormat.GEOTIFF,
                    mime_type="image/tiff",
                    size_bytes=1,
                    sha256_checksum="a" * 64,
                    storage_path=f"retry-{starting_retry_count}",
                    uploaded_by=self.engineer,
                )
                processing_job = ProcessingJob.objects.create(
                    file=survey_file,
                    status="queued",
                    retry_count=starting_retry_count,
                )
                fake_task = make_fake_task(f"automatic-retry-task-{starting_retry_count}")
                def fail_after_claim(*, processing_job_id, lease_token):
                    mark_job_running_with_lease(processing_job, lease_token)
                    raise ProcessingError("File processing failed.")

                _mocked_execute.side_effect = fail_after_claim
                fake_task.retry.side_effect = RuntimeError("retry scheduled")

                with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
                    _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

                processing_job.refresh_from_db()
                self.assertEqual(processing_job.retry_count, starting_retry_count + 1)
                self.assertEqual(fake_task.retry.call_args.kwargs["countdown"], expected_countdown)

    @patch("apps.processing.tasks.execute_processing_task", side_effect=ProcessingError("File processing failed."))
    def test_fourth_failed_attempt_marks_job_file_and_survey_failed(self, _mocked_execute):
        survey_file, processing_job, _raw_bytes = self.create_file_and_job()
        processing_job.retry_count = MAX_AUTOMATIC_RETRIES
        processing_job.save(update_fields=["retry_count", "updated_at"])
        fake_task = make_fake_task("final-failure-task")
        def fail_after_claim(*, processing_job_id, lease_token):
            mark_job_running_with_lease(processing_job, lease_token)
            raise ProcessingError("File processing failed.")

        _mocked_execute.side_effect = fail_after_claim

        result = _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        survey_file.refresh_from_db()
        self.survey.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(processing_job.status, "failed")
        self.assertEqual(survey_file.status, "failed")
        self.assertEqual(self.survey.status, SurveyStatus.FAILED)
        self.assertEqual(self.survey.processing_status, "failed")
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_FAILED).count(),
            1,
        )
        fake_task.retry.assert_not_called()

    @patch("apps.processing.services.dispatch.dispatch_processing_job_safely")
    @patch("apps.processing.services.execution._process_raster_file")
    @patch("apps.processing.services.artifacts.validate_upload")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_manual_retry_enforces_permission_state_and_resets_automatic_cycle(self, storage_factory, mocked_validate, mocked_process_raster, mocked_dispatch):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        mocked_process_raster.return_value = SimpleNamespace(preview_path="preview", converted_path="cog")

        processing_job.status = "failed"
        processing_job.save(update_fields=["status", "updated_at"])

        with self.assertRaisesMessage(
            PermissionDenied,
            "Only eligible active users can retry failed processing jobs.",
        ):
            manual_retry_processing_job(actor=self.viewer, processing_job_id=processing_job.pk)

        processing_job.status = "completed"
        processing_job.save(update_fields=["status", "updated_at"])
        with self.assertRaisesMessage(
            ValidationError,
            "Only permanently failed processing jobs can be retried manually.",
        ):
            manual_retry_processing_job(actor=self.admin, processing_job_id=processing_job.pk)

        processing_job.status = "failed"
        processing_job.retry_count = MAX_AUTOMATIC_RETRIES
        processing_job.progress_percent = 55
        processing_job.error_message = "Old error"
        processing_job.dispatch_status = "failed"
        survey_file.preview_path = "old-preview"
        survey_file.converted_path = "old-converted"
        processing_job.save(
            update_fields=[
                "retry_count",
                "status",
                "progress_percent",
                "error_message",
                "dispatch_status",
                "updated_at",
            ]
        )
        survey_file.save(update_fields=["preview_path", "converted_path", "updated_at"])

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            retried_job = manual_retry_processing_job(actor=self.engineer, processing_job_id=processing_job.pk)

        retried_job.refresh_from_db()
        survey_file.refresh_from_db()
        self.survey.refresh_from_db()
        self.assertEqual(retried_job.status, "queued")
        self.assertEqual(retried_job.retry_count, 0)
        self.assertEqual(retried_job.progress_percent, 0)
        self.assertIsNone(retried_job.error_message)
        self.assertEqual(retried_job.dispatch_status, "pending")
        self.assertEqual(survey_file.status, "uploading")
        self.assertIsNone(survey_file.preview_path)
        self.assertIsNone(survey_file.converted_path)
        self.assertEqual(self.survey.status, SurveyStatus.UPLOADING)
        self.assertEqual(self.survey.processing_status, "queued")
        self.assertEqual(len(callbacks), 1)
        mocked_dispatch.assert_not_called()
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(),
            1,
        )
        audit_details = AuditLog.objects.get(action=AuditAction.PROCESSING_RETRY).details
        self.assertEqual(audit_details["retry_count"], 0)
        self.assertEqual(audit_details["previous_retry_count"], MAX_AUTOMATIC_RETRIES)
        self.assertFalse(audit_details["automatic"])
        self.assertTrue(audit_details["starts_new_automatic_cycle"])

    def test_expired_running_job_recovery_requeues_and_dispatches_with_audit(self):
        _survey_file, processing_job, _raw_bytes = self.create_file_and_job(file_format=FileFormat.PNG)
        expired_at = timezone.now() - timedelta(minutes=1)
        ProcessingJob.objects.filter(pk=processing_job.pk).update(
            status="running",
            retry_count=1,
            lease_token="lost-worker",
            lease_expires_at=expired_at,
            last_heartbeat_at=expired_at - timedelta(minutes=10),
        )

        with patch("apps.processing.tasks.process_2d_file.apply_async") as mocked_apply_async:
            mocked_apply_async.return_value = SimpleNamespace(id="recovered-task")
            result = reconcile_expired_running_processing_jobs()

        processing_job.refresh_from_db()
        self.assertEqual(
            result,
            {"recovered": 1, "dispatched": 1, "failed_dispatches": 0, "failed_permanently": 0},
        )
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(processing_job.retry_count, 2)
        self.assertEqual(processing_job.dispatch_status, "dispatched")
        self.assertEqual(processing_job.celery_task_id, "recovered-task")
        self.assertIsNone(processing_job.lease_token)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditAction.PROCESSING_RECOVERY,
                details__recovery_type="expired_running_lease",
            ).count(),
            1,
        )

    def test_active_running_job_with_live_lease_is_not_recovered(self):
        _survey_file, processing_job, _raw_bytes = self.create_file_and_job(file_format=FileFormat.PNG)
        ProcessingJob.objects.filter(pk=processing_job.pk).update(
            status="running",
            lease_token="active-worker",
            lease_expires_at=timezone.now() + timedelta(minutes=10),
        )

        with patch("apps.processing.tasks.process_2d_file.apply_async") as mocked_apply_async:
            result = reconcile_expired_running_processing_jobs()

        self.assertEqual(
            result,
            {"recovered": 0, "dispatched": 0, "failed_dispatches": 0, "failed_permanently": 0},
        )
        mocked_apply_async.assert_not_called()
