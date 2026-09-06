from .support import *


class ProcessingPointCloudMixin:
    @patch("apps.processing.services.model_processing.subprocess.run")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_missing_potree_converter_fails_safely_without_invocation(self, storage_factory, mocked_run):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(file_format=FileFormat.LAZ, content=b"LASF\x00\x00\x00\x00")
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})

        fake_task = make_fake_task("missing-potree-task")
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            with override_settings(POTREE_CONVERTER_PATH="missing-potree"):
                _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.retry_count, 1)
        mocked_run.assert_not_called()
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_RETRY).count(),
            1,
        )

    @patch("apps.processing.services.artifacts.validate_upload")
    @patch("apps.processing.services.model_processing.subprocess.run")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_potree_processing_uploads_recursive_output_and_sets_metadata_path(
        self,
        storage_factory,
        mocked_run,
        mocked_validate,
    ):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(
            file_format=FileFormat.LAZ,
            content=b"LASF\x00\x00\x00\x00",
        )
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage

        def fake_run(args, check, capture_output):
            output_dir = Path(args[-1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / POTREE_METADATA_FILENAME).write_text('{"version":"2.0"}', encoding="utf-8")
            hierarchy_dir = output_dir / "hierarchy"
            hierarchy_dir.mkdir()
            (hierarchy_dir / "0.bin").write_bytes(b"hierarchy")
            return SimpleNamespace(returncode=0)

        mocked_run.side_effect = fake_run

        with override_settings(POTREE_CONVERTER_PATH=self.existing_local_path()):
            result = execute_processing_task(processing_job_id=processing_job.pk)

        survey_file.refresh_from_db()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            survey_file.preview_path,
            f"{self.published_root(survey_file)}potree/{POTREE_METADATA_FILENAME}",
        )
        self.assertEqual(
            survey_file.converted_path,
            f"{self.published_root(survey_file)}potree/{POTREE_METADATA_FILENAME}",
        )
        uploaded_keys = [key for key, _content_type in fake_storage.upload_calls]
        self.assertIn(
            f"{self.published_root(survey_file)}potree/{POTREE_METADATA_FILENAME}",
            uploaded_keys,
        )
        self.assertIn(
            f"{self.published_root(survey_file)}potree/hierarchy/0.bin",
            uploaded_keys,
        )
        mocked_validate.assert_called_once()

    @patch("apps.processing.services.artifacts.validate_upload")
    @patch("apps.processing.services.model_processing.subprocess.run")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_potree_processing_requires_metadata_output(
        self,
        storage_factory,
        mocked_run,
        mocked_validate,
    ):
        survey_file, processing_job, raw_bytes = self.create_file_and_job(
            file_format=FileFormat.LAS,
            content=b"LASF\x00\x00\x00\x00",
        )
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage

        def fake_run(args, check, capture_output):
            output_dir = Path(args[-1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "octree.bin").write_bytes(b"missing-metadata")
            return SimpleNamespace(returncode=0)

        mocked_run.side_effect = fake_run
        fake_task = make_fake_task("missing-potree-metadata-task")
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            with override_settings(POTREE_CONVERTER_PATH=self.existing_local_path()):
                _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.retry_count, 1)
        self.assertEqual(fake_storage.upload_calls, [])
        mocked_validate.assert_called_once()
