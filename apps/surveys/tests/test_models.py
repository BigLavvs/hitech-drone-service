from datetime import date

from django.contrib.gis.geos import Point
from django.test import TestCase

from apps.access_control.models import User, UserRole
from apps.projects.models import Project, Site
from apps.surveys.models import Survey, SurveyStatus


class SurveyModelTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            email="creator@example.com",
            external_id="creator-1",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.approver = User.objects.create_user(
            email="approver@example.com",
            external_id="approver-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.project = Project.objects.create(name="Lekki Deep Scan")
        self.site = Site.objects.create(
            project=self.project,
            name="Zone A",
            coordinates=Point(3.3903, 6.4474, srid=4326),
        )

    def test_documented_defaults_are_applied(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Baseline Capture",
            survey_date=date(2026, 8, 9),
        )

        self.assertEqual(survey.status, SurveyStatus.DRAFT)
        self.assertEqual(survey.processing_status, "pending")
        self.assertEqual(survey.coordinate_reference_system, "EPSG:4326")

    def test_project_site_creator_and_approver_relationships(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Progress Capture",
            survey_date=date(2026, 8, 8),
            created_by=self.creator,
            approved_by=self.approver,
        )

        self.assertEqual(survey.project, self.project)
        self.assertEqual(survey.site, self.site)
        self.assertEqual(survey.created_by, self.creator)
        self.assertEqual(survey.approved_by, self.approver)
        self.assertEqual(self.project.surveys.get(), survey)
        self.assertEqual(self.site.surveys.get(), survey)
        self.assertEqual(self.creator.surveys_created.get(), survey)
        self.assertEqual(self.approver.surveys_approved.get(), survey)

    def test_deleting_site_cascades_to_surveys(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Weekly Capture",
            survey_date=date(2026, 8, 7),
        )

        self.site.delete()

        self.assertFalse(Survey.objects.filter(pk=survey.pk).exists())

    def test_deleting_creator_or_approver_sets_corresponding_fields_to_null(self):
        survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Approval Capture",
            survey_date=date(2026, 8, 6),
            created_by=self.creator,
            approved_by=self.approver,
        )

        self.creator.delete()
        survey.refresh_from_db()
        self.assertIsNone(survey.created_by)
        self.assertEqual(survey.approved_by, self.approver)

        self.approver.delete()
        survey.refresh_from_db()
        self.assertIsNone(survey.approved_by)

    def test_every_documented_status_value_is_accepted(self):
        accepted_statuses = {choice for choice, _ in SurveyStatus.choices}

        self.assertEqual(
            accepted_statuses,
            {
                SurveyStatus.DRAFT,
                SurveyStatus.UPLOADING,
                SurveyStatus.PROCESSING,
                SurveyStatus.READY,
                SurveyStatus.FAILED,
                SurveyStatus.PENDING_APPROVAL,
                SurveyStatus.APPROVED,
                SurveyStatus.REJECTED,
                SurveyStatus.ARCHIVED,
            },
        )

        for index, status in enumerate(SurveyStatus.values, start=1):
            survey = Survey.objects.create(
                project=self.project,
                site=self.site,
                name=f"Survey {index}",
                survey_date=date(2026, 8, index),
                status=status,
            )
            self.assertEqual(survey.status, status)
