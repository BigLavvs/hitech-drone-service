from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.db import OperationalError
from django.test import SimpleTestCase
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.files.object_keys import build_map_tile_key, build_model_metadata_key
from apps.processing.models import ProcessingJob
from apps.processing.services.lease import (
    AttemptStorage, ProcessingHeartbeat, ProcessingLeaseLost, refresh_processing_lease,
)
from apps.processing.services.retry import (
    _mark_job_completed, _mark_job_running, _set_job_progress,
    mark_processing_failed_permanently, schedule_processing_retry,
)
from apps.processing.tasks import _run_processing_task
from .support import FakePrivateStorageAdapter, make_fake_task


class LeaseSafetyTests(SimpleTestCase):
    def test_failed_renewal_stops_work_and_stays_lost(self):
        for outcome in (False, OperationalError("database unavailable")):
            with self.subTest(outcome=type(outcome).__name__):
                heartbeat = ProcessingHeartbeat(processing_job_id=1, lease_token="old")
                options = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch("apps.processing.services.lease.refresh_processing_lease", **options) as renew:
                    with self.assertRaises(ProcessingLeaseLost):
                        heartbeat.check()
                    with self.assertRaises(ProcessingLeaseLost):
                        heartbeat.check()
                    renew.assert_called_once()

    def test_background_database_failure_is_visible_and_connection_is_closed(self):
        heartbeat = ProcessingHeartbeat(processing_job_id=1, lease_token="old")
        heartbeat._stop = Mock()
        heartbeat._stop.wait.return_value = False
        with patch("apps.processing.services.lease.close_old_connections"), patch(
            "apps.processing.services.lease.connections.close_all"
        ) as close, patch(
            "apps.processing.services.lease.refresh_processing_lease", side_effect=OperationalError("offline")
        ):
            heartbeat._run()
            with self.assertRaises(ProcessingLeaseLost):
                heartbeat.check()
        close.assert_called_once()

    def test_progress_database_failure_is_treated_as_lost_ownership(self):
        with patch(
            "apps.processing.services.retry.ProcessingJob.objects.filter"
        ) as filtered:
            filtered.return_value.filter.return_value.update.side_effect = OperationalError("offline")
            with self.assertRaises(ProcessingLeaseLost):
                _set_job_progress(processing_job_id=1, lease_token="worker", progress_percent=50)

    def test_completion_database_failure_cannot_enter_retry_or_failure_path(self):
        worker = SimpleNamespace(
            pk=1,
            file_id=3,
            file=SimpleNamespace(survey_id=2, id=3),
        )
        heartbeat = Mock()
        heartbeat_context = MagicMock()
        heartbeat_context.__enter__.return_value = heartbeat
        with patch(
            "apps.processing.services.execution._mark_job_running",
            return_value=worker,
        ), patch(
            "apps.processing.services.execution._process_file",
            return_value=SimpleNamespace(preview_path=None, converted_path=None),
        ), patch(
            "apps.processing.services.execution._mark_job_completed",
            side_effect=OperationalError("offline"),
        ), patch(
            "apps.processing.services.execution._processing_heartbeat",
            return_value=heartbeat_context,
        ), patch("apps.processing.services.execution.PrivateR2StorageAdapter"):
            with self.assertRaises(ProcessingLeaseLost):
                from apps.processing.services.execution import execute_processing_task

                execute_processing_task(
                    processing_job_id=1,
                    lease_token="worker",
                )

    def test_task_exits_on_completion_database_failure_without_retry_or_failure_mutations(self):
        worker = SimpleNamespace(
            pk=1,
            file_id=3,
            file=SimpleNamespace(survey_id=2, id=3),
        )
        heartbeat = Mock()
        heartbeat_context = MagicMock()
        heartbeat_context.__enter__.return_value = heartbeat
        task = make_fake_task("completion-database-failure-task")
        with patch(
            "apps.processing.services.execution._mark_job_running",
            return_value=worker,
        ), patch(
            "apps.processing.services.execution._process_file",
            return_value=SimpleNamespace(preview_path=None, converted_path=None),
        ), patch(
            "apps.processing.services.execution._mark_job_completed",
            side_effect=OperationalError("offline"),
        ), patch(
            "apps.processing.services.execution._processing_heartbeat",
            return_value=heartbeat_context,
        ), patch("apps.processing.services.execution.PrivateR2StorageAdapter"), patch(
            "apps.processing.tasks.schedule_processing_retry"
        ) as schedule_retry, patch(
            "apps.processing.tasks.mark_processing_failed_permanently"
        ) as mark_failed:
            result = _run_processing_task(task=task, processing_job_id=1)

        self.assertEqual(result, {"status": "ignored"})
        schedule_retry.assert_not_called()
        mark_failed.assert_not_called()
        task.retry.assert_not_called()

    def test_late_upload_cannot_overwrite_replacement_attempt(self):
        storage = FakePrivateStorageAdapter()
        old_guard, new_guard = Mock(), Mock()
        old = AttemptStorage(storage=storage, heartbeat=old_guard, survey_id=1, file_id=2)
        new = AttemptStorage(storage=storage, heartbeat=new_guard, survey_id=1, file_id=2)
        logical = "surveys/1/files/2/preview.glb"
        upload = storage.upload_generated_fileobj

        def overlapping_upload(**kwargs):
            # The newer worker finishes while the old network request is in flight.
            upload(destination_key=new.published_path(logical), file_obj=BytesIO(b"new"), content_type="model/gltf-binary")
            upload(**kwargs)

        storage.upload_generated_fileobj = overlapping_upload
        old_guard.check.side_effect = [None, ProcessingLeaseLost("recovered")]
        with self.assertRaises(ProcessingLeaseLost):
            old.upload_generated_fileobj(destination_key=logical, file_obj=BytesIO(b"old"), content_type="model/gltf-binary")
        self.assertNotEqual(old.prefix, new.prefix)
        self.assertEqual(storage.objects[new.published_path(logical)], b"new")
        self.assertEqual(storage.objects[old.published_path(logical)], b"old")
        self.assertNotIn(logical, storage.objects)

    def test_lost_worker_cannot_start_another_upload(self):
        storage, guard = Mock(), Mock()
        guard.check.side_effect = ProcessingLeaseLost("recovered")
        attempt = AttemptStorage(storage=storage, heartbeat=guard, survey_id=1, file_id=2)
        with self.assertRaises(ProcessingLeaseLost):
            attempt.upload_generated_fileobj(destination_key="surveys/1/files/2/model.glb", file_obj=BytesIO(), content_type="model/gltf-binary")
        storage.upload_generated_fileobj.assert_not_called()

    def test_redelivery_gets_distinct_execution_tokens(self):
        task = make_fake_task("same-broker-message")
        with patch("apps.processing.tasks.execute_processing_task", return_value={"status": "ignored"}) as execute:
            _run_processing_task(task=task, processing_job_id=1)
            _run_processing_task(task=task, processing_job_id=1)
        tokens = [call.kwargs["lease_token"] for call in execute.call_args_list]
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertNotIn(task.request.id, tokens)

    def test_key_builders_support_published_attempt_and_legacy_layout(self):
        root = "surveys/1/files/2/"
        attempt = root + "attempts/" + "a" * 32 + "/"
        for prefix in (root, attempt):
            with self.subTest(prefix=prefix):
                self.assertEqual(build_model_metadata_key(survey_id=1, file_id=2, published_path=prefix + "preview.glb"), prefix + "model-metadata.json")
                self.assertEqual(build_map_tile_key(survey_id=1, file_id=2, z=3, x=4, y=5, published_path=prefix + "preview.png"), prefix + "tiles/3/4/5.png")


class ProcessingLeaseSafetyMixin:
    def test_duplicate_queued_delivery_only_one_execution_claims_lease(self):
        _survey_file, processing_job, _raw_bytes = self.create_file_and_job()

        first = _mark_job_running(processing_job_id=processing_job.pk, lease_token="first")
        second = _mark_job_running(processing_job_id=processing_job.pk, lease_token="second")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        processing_job.refresh_from_db()
        self.assertEqual(processing_job.lease_token, "first")
        self.assertEqual(
            AuditLog.objects.filter(action=AuditAction.PROCESSING_STARTED).count(),
            1,
        )

    def test_expired_worker_cannot_renew_publish_retry_fail_or_update_progress(self):
        survey_file, job, _ = self.create_file_and_job()
        _mark_job_running(processing_job_id=job.pk, lease_token="expired")
        ProcessingJob.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(refresh_processing_lease(processing_job_id=job.pk, lease_token="expired"))
        self.assertFalse(_mark_job_completed(processing_job_id=job.pk, lease_token="expired", preview_path="stale", converted_path=None))
        self.assertIsNone(schedule_processing_retry(processing_job_id=job.pk, lease_token="expired", error_message="stale"))
        self.assertFalse(mark_processing_failed_permanently(processing_job_id=job.pk, lease_token="expired", error_message="stale"))
        with self.assertRaises(ProcessingLeaseLost):
            _set_job_progress(processing_job_id=job.pk, lease_token="expired", progress_percent=99)
        job.refresh_from_db()
        survey_file.refresh_from_db()
        self.assertEqual(job.status, "running")
        self.assertEqual(job.retry_count, 0)
        self.assertEqual(job.progress_percent, 10)
        self.assertIsNone(survey_file.preview_path)
        self.assertEqual(list(AuditLog.objects.values_list("action", flat=True)), [AuditAction.PROCESSING_STARTED])

    def test_old_token_cannot_publish_after_replacement_claims(self):
        survey_file, job, _ = self.create_file_and_job()
        _mark_job_running(processing_job_id=job.pk, lease_token="replacement")
        self.assertFalse(_mark_job_completed(processing_job_id=job.pk, lease_token="old", preview_path="old", converted_path=None))
        self.assertTrue(_mark_job_completed(processing_job_id=job.pk, lease_token="replacement", preview_path="winner", converted_path=None))
        self.assertFalse(_mark_job_completed(processing_job_id=job.pk, lease_token="old", preview_path="late", converted_path=None))
        survey_file.refresh_from_db()
        self.assertEqual(survey_file.preview_path, "winner")
        self.assertEqual(AuditLog.objects.filter(action=AuditAction.PROCESSING_COMPLETED).count(), 1)

    def test_lost_lease_before_generation_returns_ignored_without_storage_access(self):
        from apps.processing.services import execute_processing_task

        _, job, _ = self.create_file_and_job()
        with patch("apps.processing.services.lease.refresh_processing_lease", return_value=False), patch(
            "apps.processing.services.execution.PrivateR2StorageAdapter"
        ) as storage:
            self.assertEqual(execute_processing_task(processing_job_id=job.pk), {"status": "ignored"})
        storage.assert_not_called()
