from datetime import date

from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import record_audit_event
from apps.audit.tasks import record_file_download_audit_event
from apps.files.models import FileFormat, FileType, SurveyFile
from apps.projects.models import Project, Site
from apps.surveys.models import Survey


class AuditLogSchemaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="auditor@example.com",
            external_id="user-ext-1",
            role=UserRole.ADMINISTRATOR,
        )
        cls.project = Project.objects.create(
            name="Audit Project",
            project_manager=cls.user,
            created_by=cls.user,
        )
        cls.site = Site.objects.create(
            project=cls.project,
            name="Audit Site",
            coordinates=Point(3.3792, 6.5244, srid=4326),
        )
        cls.survey = Survey.objects.create(
            project=cls.project,
            site=cls.site,
            name="Audit Survey",
            survey_date=date(2026, 8, 9),
            created_by=cls.user,
        )

    def test_audit_record_persists_documented_fields(self):
        details = {"status": "ready", "file_count": 2}
        audit_log = record_audit_event(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=self.survey.pk,
            user=self.user,
            project=self.project,
            survey=self.survey,
            details=details,
            ip_address="192.168.1.10",
        )

        persisted = AuditLog.objects.get(pk=audit_log.pk)

        self.assertEqual(persisted.action, AuditAction.SURVEY_CREATED)
        self.assertEqual(persisted.entity_type, "survey")
        self.assertEqual(persisted.entity_id, self.survey.pk)
        self.assertEqual(persisted.details, details)
        self.assertEqual(persisted.ip_address, "192.168.1.10")
        self.assertIsNotNone(persisted.timestamp)

    def test_invalid_audit_action_is_rejected_without_creating_a_record(self):
        with self.assertRaisesMessage(ValidationError, "Invalid audit action."):
            record_audit_event(
                action="NOT_A_REAL_AUDIT_ACTION",
                entity_type="survey",
                entity_id=self.survey.pk,
            )

        self.assertEqual(AuditLog.objects.count(), 0)

    def test_existing_audit_record_cannot_be_saved_after_modification(self):
        audit_log = record_audit_event(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=self.survey.pk,
        )

        audit_log.details = {"status": "changed"}

        with self.assertRaisesMessage(
            ValidationError,
            "Audit records are append-only and cannot be updated.",
        ):
            audit_log.save()

    def test_existing_audit_record_cannot_be_deleted_directly(self):
        audit_log = record_audit_event(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=self.survey.pk,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Audit records are append-only and cannot be deleted.",
        ):
            audit_log.delete()

    def test_user_deletion_sets_audit_foreign_key_to_null(self):
        audit_log = record_audit_event(
            action=AuditAction.ADMIN_ACTION,
            entity_type="user",
            entity_id=self.user.pk,
            user=self.user,
        )

        self.user.delete()
        audit_log.refresh_from_db()

        self.assertIsNone(audit_log.user)

    def test_project_deletion_sets_audit_foreign_key_to_null(self):
        audit_log = record_audit_event(
            action=AuditAction.PROJECT_CREATED,
            entity_type="project",
            entity_id=self.project.pk,
            project=self.project,
        )

        self.project.delete()
        audit_log.refresh_from_db()

        self.assertIsNone(audit_log.project)

    def test_survey_deletion_sets_audit_foreign_key_to_null(self):
        audit_log = record_audit_event(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=self.survey.pk,
            survey=self.survey,
        )

        self.survey.delete()
        audit_log.refresh_from_db()

        self.assertIsNone(audit_log.survey)

    def test_documented_indexes_exist(self):
        constraints = connection.introspection.get_constraints(
            connection.cursor(),
            AuditLog._meta.db_table,
        )
        actual_indexes = {
            tuple(constraint["columns"])
            for constraint in constraints.values()
            if constraint["index"]
        }

        self.assertTrue(
            {
                ("action",),
                ("entity_type", "entity_id"),
                ("user_id",),
                ("project_id",),
                ("survey_id",),
                ("timestamp",),
            }.issubset(actual_indexes)
        )

    def test_action_choices_match_documented_values(self):
        self.assertEqual(
            {value for value, _label in AuditAction.choices},
            {
                "PROJECT_CREATED",
                "PROJECT_UPDATED",
                "PROJECT_ARCHIVED",
                "SITE_CREATED",
                "SITE_UPDATED",
                "SITE_DELETED",
                "SURVEY_CREATED",
                "SURVEY_UPDATED",
                "SURVEY_SUBMITTED",
                "FILE_UPLOADED",
                "FILE_UPLOAD_FAILED",
                "PROCESSING_STARTED",
                "PROCESSING_FAILED",
                "PROCESSING_RETRY",
                "PROCESSING_COMPLETED",
                "APPROVAL_SUBMITTED",
                "APPROVAL_APPROVED",
                "APPROVAL_REJECTED",
                "SURVEY_ARCHIVED",
                "FILE_DOWNLOADED",
                "MEASUREMENT_CREATED",
                "MEASUREMENT_DELETED",
                "ADMIN_ACTION",
            },
        )

    def test_latest_migration_state_includes_processing_completed_action(self):
        loader = MigrationLoader(connection)
        state = loader.project_state(("audit", "0003_alter_auditlog_action"))
        action_field = state.apps.get_model("audit", "AuditLog")._meta.get_field("action")

        self.assertIn(("PROCESSING_COMPLETED", "Processing Completed"), action_field.choices)

    def test_file_download_task_writes_required_immutable_audit_row_without_url_or_path_details(self):
        survey_file = SurveyFile.objects.create(
            survey=self.survey,
            original_filename="audit-download.tif",
            stored_filename="audit-download.tif",
            file_type=FileType.TWO_D,
            format=FileFormat.GEOTIFF,
            mime_type="image/tiff",
            size_bytes=4096,
            sha256_checksum="a" * 64,
            storage_path=f"surveys/{self.survey.pk}/files/45/raw.tif",
            status="ready",
            uploaded_by=self.user,
        )

        record_file_download_audit_event(
            user_id=self.user.pk,
            project_id=self.project.pk,
            survey_id=self.survey.pk,
            survey_file_id=survey_file.pk,
        )

        audit_log = AuditLog.objects.get(action=AuditAction.FILE_DOWNLOADED)
        self.assertEqual(audit_log.entity_type, "survey_file")
        self.assertEqual(audit_log.entity_id, survey_file.pk)
        self.assertEqual(audit_log.user, self.user)
        self.assertEqual(audit_log.project, self.project)
        self.assertEqual(audit_log.survey, self.survey)
        self.assertTrue(audit_log.details in (None, {}))
