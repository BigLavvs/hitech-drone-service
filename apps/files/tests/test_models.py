from .support import *


class FileModelsTestCase(TestCase):
    def setUp(self):
        self.uploader = User.objects.create_user(
            email="uploader@example.com",
            external_id="uploader-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(name="Inspection Corridor")
        self.site = Site.objects.create(
            project=self.project,
            name="South Block",
            coordinates=Point(3.4211, 6.4512, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Morning Capture",
            survey_date=date(2026, 8, 9),
        )
        self.other_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Afternoon Capture",
            survey_date=date(2026, 8, 8),
        )

    def create_survey_file(self, **overrides):
        payload = {
            "survey": self.survey,
            "original_filename": "orthomosaic.tif",
            "stored_filename": "survey-1-orthomosaic.tif",
            "file_type": FileType.TWO_D,
            "format": FileFormat.GEOTIFF,
            "mime_type": "image/tiff",
            "size_bytes": 2048,
            "sha256_checksum": "a" * 64,
            "storage_path": "surveys/1/raw/orthomosaic.tif",
            "preview_path": "surveys/1/previews/orthomosaic.jpg",
            "converted_path": "surveys/1/converted/orthomosaic-cog.tif",
            "uploaded_by": self.uploader,
        }
        payload.update(overrides)
        return SurveyFile.objects.create(**payload)

    def create_survey_file_asset(self, **overrides):
        survey_file = overrides.pop("survey_file", None) or self.create_survey_file()
        payload = {
            "survey_file": survey_file,
            "original_filename": "materials.mtl",
            "stored_filename": "materials.mtl",
            "mime_type": "text/plain",
            "size_bytes": 1024,
            "sha256_checksum": "b" * 64,
            "storage_path": f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/assets/materials.mtl",
        }
        payload.update(overrides)
        return SurveyFileAsset.objects.create(**payload)

    def test_survey_file_metadata_paths_uploader_and_default_status(self):
        survey_file = self.create_survey_file()

        self.assertEqual(survey_file.survey, self.survey)
        self.assertEqual(survey_file.original_filename, "orthomosaic.tif")
        self.assertEqual(survey_file.stored_filename, "survey-1-orthomosaic.tif")
        self.assertEqual(survey_file.file_type, FileType.TWO_D)
        self.assertEqual(survey_file.format, FileFormat.GEOTIFF)
        self.assertEqual(survey_file.mime_type, "image/tiff")
        self.assertEqual(survey_file.size_bytes, 2048)
        self.assertEqual(survey_file.sha256_checksum, "a" * 64)
        self.assertEqual(survey_file.storage_path, "surveys/1/raw/orthomosaic.tif")
        self.assertEqual(survey_file.preview_path, "surveys/1/previews/orthomosaic.jpg")
        self.assertEqual(
            survey_file.converted_path,
            "surveys/1/converted/orthomosaic-cog.tif",
        )
        self.assertEqual(survey_file.status, "uploading")
        self.assertEqual(survey_file.uploaded_by, self.uploader)
        self.assertEqual(self.survey.files.get(), survey_file)
        self.assertEqual(self.uploader.files_uploaded.get(), survey_file)

    def test_duplicate_checksum_is_rejected_within_survey_but_allowed_across_surveys(self):
        self.create_survey_file()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_survey_file(
                    stored_filename="survey-1-orthomosaic-copy.tif",
                    storage_path="surveys/1/raw/orthomosaic-copy.tif",
                )

        duplicate_on_other_survey = self.create_survey_file(
            survey=self.other_survey,
            stored_filename="survey-2-orthomosaic.tif",
            storage_path="surveys/2/raw/orthomosaic.tif",
        )

        self.assertEqual(duplicate_on_other_survey.survey, self.other_survey)

    def test_upload_session_defaults_and_one_to_one_file_relationship(self):
        survey_file = self.create_survey_file()
        session = UploadSession.objects.create(
            id="upload-session-1",
            survey=self.survey,
            file=survey_file,
            file_type=FileType.TWO_D,
            total_size_bytes=4096,
        )

        self.assertEqual(session.uploaded_bytes, 0)
        self.assertEqual(session.progress_percent, 0)
        self.assertEqual(session.status, "in_progress")
        self.assertIsNone(session.checksum_expected)
        self.assertEqual(session.file, survey_file)
        self.assertEqual(survey_file.upload_session, session)
        self.assertEqual(self.survey.upload_sessions.get(), session)

    def test_survey_file_asset_persists_metadata_and_relationship(self):
        survey_file = self.create_survey_file()
        asset = self.create_survey_file_asset(survey_file=survey_file)

        self.assertEqual(asset.survey_file, survey_file)
        self.assertEqual(asset.original_filename, "materials.mtl")
        self.assertEqual(asset.stored_filename, "materials.mtl")
        self.assertEqual(asset.mime_type, "text/plain")
        self.assertEqual(asset.size_bytes, 1024)
        self.assertEqual(asset.sha256_checksum, "b" * 64)
        self.assertEqual(
            asset.storage_path,
            f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/assets/materials.mtl",
        )
        self.assertEqual(survey_file.assets.get(), asset)

    def test_survey_file_asset_indexes_and_uniqueness_constraints_are_enforced(self):
        survey_file = self.create_survey_file()
        other_survey_file = self.create_survey_file(
            survey=self.other_survey,
            stored_filename="survey-2-orthomosaic.tif",
            storage_path="surveys/2/raw/orthomosaic.tif",
            sha256_checksum="c" * 64,
        )
        self.create_survey_file_asset(survey_file=survey_file)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_survey_file_asset(
                    survey_file=survey_file,
                    stored_filename="other.mtl",
                    sha256_checksum="b" * 64,
                    storage_path=f"surveys/{survey_file.survey_id}/files/{survey_file.pk}/assets/other.mtl",
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_survey_file_asset(
                    survey_file=survey_file,
                    stored_filename="other.mtl",
                    sha256_checksum="d" * 64,
                )

        duplicate_name_on_other_file = self.create_survey_file_asset(
            survey_file=other_survey_file,
            stored_filename="materials.mtl",
            storage_path=f"surveys/{other_survey_file.survey_id}/files/{other_survey_file.pk}/assets/materials.mtl",
        )
        self.assertEqual(duplicate_name_on_other_file.survey_file, other_survey_file)

        constraints = {
            constraint.name
            for constraint in SurveyFileAsset._meta.constraints
        }
        self.assertIn("survey_file_asset_unique_name_per_file", constraints)
        self.assertIn("survey_file_asset_unique_checksum_per_file", constraints)
        self.assertIn("survey_file_asset_unique_path_per_file", constraints)
        index_fields = {tuple(index.fields) for index in SurveyFileAsset._meta.indexes}
        self.assertIn(("survey_file",), index_fields)
        self.assertIn(("sha256_checksum",), index_fields)

    def test_deleting_survey_cascades_to_survey_files_and_upload_sessions(self):
        survey_file = self.create_survey_file()
        asset = self.create_survey_file_asset(survey_file=survey_file)
        session = UploadSession.objects.create(
            id="upload-session-2",
            survey=self.survey,
            file=survey_file,
            file_type=FileType.TWO_D,
            total_size_bytes=4096,
        )

        self.survey.delete()

        self.assertFalse(SurveyFile.objects.filter(pk=survey_file.pk).exists())
        self.assertFalse(SurveyFileAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(UploadSession.objects.filter(pk=session.pk).exists())

    def test_deleting_survey_file_cascades_to_related_assets(self):
        survey_file = self.create_survey_file()
        asset = self.create_survey_file_asset(survey_file=survey_file)

        survey_file.delete()

        self.assertFalse(SurveyFileAsset.objects.filter(pk=asset.pk).exists())

    def test_deleting_survey_file_sets_linked_upload_session_file_to_null(self):
        survey_file = self.create_survey_file()
        session = UploadSession.objects.create(
            id="upload-session-3",
            survey=self.survey,
            file=survey_file,
            file_type=FileType.TWO_D,
            total_size_bytes=4096,
        )

        survey_file.delete()
        session.refresh_from_db()

        self.assertIsNone(session.file)
