from .support import *
from .test_upload_admission_assets import UploadAdmissionAssetsMixin
from .test_upload_admission_authorization import UploadAdmissionAuthorizationMixin
from .test_upload_admission_persistence import UploadAdmissionPersistenceMixin


class UploadAdmissionServiceTests(UploadAdmissionAuthorizationMixin, UploadAdmissionPersistenceMixin, UploadAdmissionAssetsMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            external_id="admin-1",
            role=UserRole.ADMINISTRATOR,
            is_staff=True,
        )
        self.owner_manager = User.objects.create_user(
            email="owner-manager@example.com",
            external_id="manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.other_manager = User.objects.create_user(
            email="other-manager@example.com",
            external_id="manager-2",
            role=UserRole.PROJECT_MANAGER,
        )
        self.assigned_engineer = User.objects.create_user(
            email="assigned-engineer@example.com",
            external_id="engineer-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.unassigned_engineer = User.objects.create_user(
            email="unassigned-engineer@example.com",
            external_id="engineer-2",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.viewer = User.objects.create_user(
            email="viewer@example.com",
            external_id="viewer-1",
            role=UserRole.VIEWER,
        )
        self.inactive_admin = User.objects.create_user(
            email="inactive-admin@example.com",
            external_id="inactive-admin-1",
            role=UserRole.ADMINISTRATOR,
            is_active=False,
            is_staff=True,
        )
        self.inactive_owner_manager = User.objects.create_user(
            email="inactive-owner-manager@example.com",
            external_id="inactive-manager-1",
            role=UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.inactive_assigned_engineer = User.objects.create_user(
            email="inactive-assigned-engineer@example.com",
            external_id="inactive-engineer-1",
            role=UserRole.SURVEY_ENGINEER,
            is_active=False,
        )
        self.project = Project.objects.create(
            name="Airport Expansion",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.archived_project = Project.objects.create(
            name="Archived Airport Expansion",
            project_manager=self.owner_manager,
            created_by=self.admin,
            status="archived",
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Runway West",
            coordinates=Point(3.4211, 6.4512, srid=4326),
        )
        self.archived_site = Site.objects.create(
            project=self.archived_project,
            name="Runway East",
            coordinates=Point(3.5111, 6.5512, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Week 1 Capture",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.DRAFT,
        )
        self.archived_project_survey = Survey.objects.create(
            project=self.archived_project,
            site=self.archived_site,
            name="Archived Week 1 Capture",
            survey_date=date(2026, 8, 8),
            status=SurveyStatus.DRAFT,
        )
        for member in (self.assigned_engineer, self.viewer, self.inactive_assigned_engineer):
            ProjectMembership.objects.create(
                project=self.project,
                user=member,
                assigned_by=self.owner_manager,
            )

    def make_upload(self, name="site_ortho.tif", content=None, content_type="image/tiff"):
        payload = content or (
            b"II*\x00\x08\x00\x00\x00\x01\x00\xAF\x87\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00"
        )
        return TrackingUpload(name=name, content=payload, content_type=content_type)

    def create_existing_file_with_job(self, **overrides):
        payload = {
            "survey": self.survey,
            "original_filename": "site_ortho.tif",
            "stored_filename": "site_ortho.tif",
            "file_type": FileType.TWO_D,
            "format": FileFormat.GEOTIFF,
            "mime_type": "image/tiff",
            "size_bytes": 2048,
            "sha256_checksum": "a" * 64,
            "storage_path": "surveys/1/files/1/raw.tif",
            "uploaded_by": self.assigned_engineer,
        }
        payload.update(overrides)
        survey_file = SurveyFile.objects.create(**payload)
        processing_job = ProcessingJob.objects.create(file=survey_file, status="queued")
        return survey_file, processing_job
