from .support import *


class ProcessingDispatchLifecycleMixin:
    def test_dispatch_selects_task_and_persists_celery_task_id(self):
        survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.GLB)

        with patch("apps.processing.tasks.process_3d_file.apply_async") as mocked_apply_async:
            mocked_apply_async.return_value = SimpleNamespace(id="task-3d-1")
            result = dispatch_processing_job(processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertTrue(result.dispatched)
        self.assertEqual(result.celery_task_id, "task-3d-1")
        self.assertEqual(processing_job.celery_task_id, "task-3d-1")
        self.assertEqual(processing_job.dispatch_status, "dispatched")
        mocked_apply_async.assert_called_once_with(args=[processing_job.pk])
        self.assertEqual(survey_file.file_type, FileType.THREE_D)

    def test_failed_dispatch_is_observable_and_recoverable(self):
        _survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.PNG)

        with patch("apps.processing.tasks.process_2d_file.apply_async", side_effect=RuntimeError("redis down")):
            result = dispatch_processing_job_safely(processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertFalse(result.dispatched)
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(processing_job.dispatch_status, "failed")
        self.assertEqual(processing_job.dispatch_failure_count, 1)
        self.assertIsNone(processing_job.celery_task_id)

    def test_late_dispatcher_cannot_overwrite_newer_retry_schedule(self):
        _survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.PNG)
        future_retry = timezone.now() + timedelta(minutes=10)

        def enqueue_then_schedule_retry(*, args):
            ProcessingJob.objects.filter(pk=processing_job.pk).update(
                dispatch_status="pending",
                dispatch_available_at=future_retry,
            )
            return SimpleNamespace(id="late-task")

        with patch("apps.processing.tasks.process_2d_file.apply_async", side_effect=enqueue_then_schedule_retry):
            result = dispatch_processing_job(processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertTrue(result.dispatched)
        self.assertEqual(processing_job.dispatch_status, "pending")
        self.assertEqual(processing_job.dispatch_available_at, future_retry)
        self.assertIsNone(processing_job.celery_task_id)

    def test_reconciliation_redispaches_stale_queued_job_without_new_processing_job(self):
        _survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.PNG)
        stale_time = timezone.now() - timedelta(seconds=settings.PROCESSING_QUEUED_DISPATCH_STALE_AFTER_SECONDS + 30)
        ProcessingJob.objects.filter(pk=processing_job.pk).update(
            dispatch_status="failed",
            dispatch_last_attempt_at=stale_time,
            updated_at=stale_time,
        )

        with patch("apps.processing.tasks.process_2d_file.apply_async") as mocked_apply_async:
            mocked_apply_async.return_value = SimpleNamespace(id="redispatch-1")
            result = reconcile_stale_queued_processing_jobs()

        processing_job.refresh_from_db()
        self.assertEqual(result, {"claimed": 1, "dispatched": 1, "failed_dispatches": 0})
        self.assertEqual(ProcessingJob.objects.count(), 1)
        self.assertEqual(processing_job.status, "queued")
        self.assertEqual(processing_job.dispatch_status, "dispatched")
        self.assertEqual(processing_job.celery_task_id, "redispatch-1")
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditAction.PROCESSING_RECOVERY,
                details__recovery_type="queued_dispatch",
            ).count(),
            1,
        )

    def test_reconciliation_does_not_dispatch_fresh_queued_job(self):
        _survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.PNG)

        with patch("apps.processing.tasks.process_2d_file.apply_async") as mocked_apply_async:
            result = reconcile_stale_queued_processing_jobs()

        processing_job.refresh_from_db()
        self.assertEqual(result, {"claimed": 0, "dispatched": 0, "failed_dispatches": 0})
        mocked_apply_async.assert_not_called()
        self.assertEqual(processing_job.dispatch_status, "pending")

    def test_completed_jobs_are_never_recovered_or_redispatched(self):
        _survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.PNG)
        stale_time = timezone.now() - timedelta(days=1)
        ProcessingJob.objects.filter(pk=processing_job.pk).update(
            status="completed",
            dispatch_status="failed",
            dispatch_last_attempt_at=stale_time,
            updated_at=stale_time,
        )

        with patch("apps.processing.tasks.process_2d_file.apply_async") as mocked_apply_async:
            result = reconcile_stale_queued_processing_jobs()

        self.assertEqual(result, {"claimed": 0, "dispatched": 0, "failed_dispatches": 0})
        mocked_apply_async.assert_not_called()

    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_task_is_idempotent_for_running_or_completed_jobs(self, storage_factory):
        storage_factory.return_value = Mock()
        survey_file, processing_job, _raw = self.create_file_and_job()

        for status in ("running", "completed"):
            with self.subTest(status=status):
                processing_job.status = status
                processing_job.save(update_fields=["status", "updated_at"])
                result = execute_processing_task(processing_job_id=processing_job.pk)
                self.assertEqual(result["status"], "ignored")

        self.assertEqual(
            AuditLog.objects.filter(
                action__in=[AuditAction.PROCESSING_STARTED, AuditAction.PROCESSING_COMPLETED]
            ).count(),
            0,
        )

    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_successful_processing_updates_states_and_audits(self, storage_factory):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage
        preview_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00\x3a\x7e\x9b\x55"
            b"\x00\x00\x00\x0cIDAT\x08\x99\x63\x60\x00\x00\x00\x02\x00\x01\xf4\x71\x64\xa6"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        class FakeDataset:
            count = 1
            height = 8
            width = 8

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, **kwargs):
                import numpy

                return FakeMaskedArray(numpy.array([[[0.0, 5.0], [10.0, 15.0]]], dtype="float32"))

        class FakePreviewWriter:
            def __init__(self, output_path):
                self.output_path = Path(output_path)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _data):
                self.output_path.write_bytes(preview_bytes)

        def fake_rasterio_open(path, mode="r", **kwargs):
            if mode == "w":
                return FakePreviewWriter(path)
            return FakeDataset()

        def fake_rasterio_copy(src, dst, driver):
            Path(dst).write_bytes(b"cog-bytes")

        with patch("rasterio.open", side_effect=fake_rasterio_open), patch(
            "rasterio.shutil.copy", side_effect=fake_rasterio_copy
        ), patch(
            "apps.processing.services.raster._generate_xyz_tile_pyramid",
            return_value=representative_tile_metadata(),
        ) as mocked_tile_generation:
            result = execute_processing_task(processing_job_id=processing_job.pk)

        preview_key = f"{self.published_root(survey_file)}preview.png"
        converted_key = f"{self.published_root(survey_file)}cog.tif"
        metadata_key = f"{self.published_root(survey_file)}tiles/metadata.json"
        processing_job.refresh_from_db()
        survey_file.refresh_from_db()
        self.survey.refresh_from_db()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(processing_job.status, "completed")
        self.assertEqual(processing_job.progress_percent, 100)
        self.assertEqual(survey_file.status, "ready")
        self.assertEqual(survey_file.preview_path, preview_key)
        self.assertEqual(survey_file.converted_path, converted_key)
        self.assertEqual(self.survey.status, SurveyStatus.READY)
        self.assertEqual(self.survey.processing_status, "completed")
        self.assertTrue(fake_storage.objects[preview_key].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(fake_storage.objects[converted_key], b"cog-bytes")
        self.assertEqual(
            fake_storage.upload_calls,
            [
                (preview_key, "image/png"),
                (converted_key, survey_file.mime_type),
                (metadata_key, "application/json"),
            ],
        )
        self.assertEqual(
            json.loads(fake_storage.objects[metadata_key].decode("utf-8")),
            representative_tile_metadata(),
        )
        mocked_tile_generation.assert_called_once()
        self.assertEqual(
            list(AuditLog.objects.values_list("action", flat=True)),
            [AuditAction.PROCESSING_STARTED, AuditAction.PROCESSING_COMPLETED],
        )

    @patch("apps.processing.services.execution._process_raster_file")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_worker_revalidation_uses_persisted_safe_filename(self, storage_factory, mocked_process_raster):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(
            original_filename="Quarterly Survey Final.tif",
        )
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        mocked_process_raster.return_value = SimpleNamespace(preview_path=f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/preview.png", converted_path=f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/cog.tif")

        with patch("apps.processing.services.artifacts.validate_upload", wraps=__import__("apps.files.validation", fromlist=["validate_upload"]).validate_upload) as mocked_validate:
            execute_processing_task(processing_job_id=processing_job.pk)

        validation_file = mocked_validate.call_args.args[0]
        self.assertEqual(validation_file.name, survey_file.original_filename)
        self.assertNotEqual(validation_file.name, survey_file.storage_path)

    @patch("apps.processing.services.artifacts.validate_upload")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_survey_remains_processing_until_all_files_are_ready(self, storage_factory, mocked_validate):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        self.create_file_and_job(
            file_format=FileFormat.PNG,
            content=b"\x89PNG\r\n\x1a\nrest",
            status="processing",
            storage_path="raw-png",
        )
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})

        with patch(
            "apps.processing.services.execution._process_raster_file",
            return_value=SimpleNamespace(preview_path=f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/preview.png", converted_path=f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/cog.tif"),
        ):
            execute_processing_task(processing_job_id=processing_job.pk)

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, SurveyStatus.PROCESSING)
        self.assertEqual(self.survey.processing_status, "processing")
        mocked_validate.assert_called_once()
