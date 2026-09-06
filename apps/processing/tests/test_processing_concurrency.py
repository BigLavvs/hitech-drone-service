import threading
from datetime import date, timedelta
from hashlib import sha256

from django.contrib.gis.geos import Point
from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from unittest.mock import patch

from apps.access_control.models import User, UserRole
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.processing.models import ProcessingJob
from apps.processing.services.retry import _mark_job_completed
from apps.projects.models import Project, Site, ProjectMembership
from apps.surveys.models import Survey, SurveyStatus


class ProcessingCompletionConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.worker_user = User.objects.create_user(
            email="engineer@example.com",
            external_id="engineer-concurrency",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project_manager = User.objects.create_user(
            email="manager@example.com",
            external_id="manager-concurrency",
            role=UserRole.PROJECT_MANAGER,
        )
        self.project = Project.objects.create(
            name="Concurrency Project",
            project_manager=self.project_manager,
            created_by=self.project_manager,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Concurrency Site",
            coordinates=Point(3.4, 6.4, srid=4326),
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.worker_user,
            assigned_by=self.project_manager,
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Last Two Jobs",
            survey_date=date(2026, 9, 5),
            created_by=self.worker_user,
            status=SurveyStatus.PROCESSING,
            processing_status="processing",
        )
        self.jobs = []
        for index in (1, 2):
            payload = f"file-{index}".encode()
            survey_file = SurveyFile.objects.create(
                survey=self.survey,
                original_filename=f"source-{index}.png",
                stored_filename=f"source-{index}.png",
                file_type=FileType.TWO_D,
                format=FileFormat.PNG,
                mime_type="image/png",
                size_bytes=len(payload),
                sha256_checksum=sha256(payload).hexdigest(),
                storage_path=f"concurrency-{index}",
                uploaded_by=self.worker_user,
                status="processing",
            )
            self.jobs.append(
                ProcessingJob.objects.create(
                    file=survey_file,
                    status="running",
                    progress_percent=50,
                    lease_token=f"worker-{index}",
                    lease_expires_at=timezone.now() + timedelta(minutes=10),
                )
            )

    def test_concurrent_completion_of_last_two_jobs_leaves_survey_ready(self):
        arrival = threading.Barrier(2)
        results = []
        errors = []

        from apps.processing.services import retry as retry_service

        original_lock = retry_service.lock_survey_for_workflow

        def synchronized_lock(*, survey_id):
            arrival.wait(timeout=10)
            return original_lock(survey_id=survey_id)

        def complete(job):
            close_old_connections()
            try:
                results.append(
                    _mark_job_completed(
                        processing_job_id=job.pk,
                        lease_token=job.lease_token,
                        preview_path=f"preview-{job.pk}",
                        converted_path=None,
                    )
                )
            except BaseException as exc:  # surfaced by the assertions below
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=complete, args=(job,), daemon=True)
            for job in self.jobs
        ]
        with patch.object(retry_service, "lock_survey_for_workflow", side_effect=synchronized_lock):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)

        self.survey.refresh_from_db()
        self.assertEqual(self.survey.status, SurveyStatus.READY)
        self.assertEqual(self.survey.processing_status, "completed")
        self.assertEqual(
            list(ProcessingJob.objects.filter(file__survey=self.survey).values_list("status", flat=True)),
            ["completed", "completed"],
        )
        self.assertEqual(
            list(SurveyFile.objects.filter(survey=self.survey).values_list("status", flat=True)),
            ["ready", "ready"],
        )
