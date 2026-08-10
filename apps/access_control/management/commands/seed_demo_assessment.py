from __future__ import annotations

from datetime import date

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access_control.demo_access import DEMO_USER_SPECS, ensure_demo_auth_enabled
from apps.access_control.models import User
from apps.maps.models import MeasurementType
from apps.maps.services import create_measurement
from apps.projects.models import Project, ProjectMembership, Site
from apps.projects.services import create_project, create_site
from apps.surveys.models import Survey
from apps.surveys.services import create_survey

DEMO_PROJECT_NAME = "Assessment Demo Project"
DEMO_SITE_NAME = "Assessment Demo Site"
DEMO_SURVEY_NAME = "Assessment Demo Survey"


class Command(BaseCommand):
    help = "Create or update the four documented demo users and a coherent development-only assessment dataset."

    def handle(self, *args, **options):
        ensure_demo_auth_enabled()

        with transaction.atomic():
            users = {key: self._create_or_update_user(spec) for key, spec in DEMO_USER_SPECS.items()}
            project = self._create_or_update_project(users)
            site = self._create_or_update_site(users, project)
            self._ensure_membership(project=project, user=users["survey_engineer"], assigned_by=users["project_manager"])
            self._ensure_membership(project=project, user=users["viewer"], assigned_by=users["project_manager"])
            survey = self._create_or_update_survey(users=users, project=project, site=site)
            self._ensure_demo_measurement(users=users, survey=survey)

        self.stdout.write(self.style.SUCCESS("Seeded demo assessment data."))
        for key, user in users.items():
            self.stdout.write(
                f"{key}: id={user.pk} email={user.email} role={user.role} external_id={user.external_id}"
            )
        self.stdout.write(
            f"project: id={project.pk} site: id={site.pk} survey: id={survey.pk} status={survey.status}"
        )

    def _create_or_update_user(self, spec):
        user, _created = User.objects.update_or_create(
            external_id=spec.external_id,
            defaults={
                "email": spec.email,
                "role": spec.role,
                "is_active": True,
                "is_staff": spec.role == DEMO_USER_SPECS["administrator"].role,
            },
        )
        return user

    def _create_or_update_project(self, users: dict[str, User]) -> Project:
        project = (
            Project.objects.select_related("project_manager", "created_by")
            .filter(name=DEMO_PROJECT_NAME, created_by=users["administrator"])
            .first()
        )
        if project is None:
            return create_project(
                actor=users["administrator"],
                name=DEMO_PROJECT_NAME,
                description="Development-only assessment project for role-based demo access.",
                location="Lagos, Nigeria",
                project_manager=users["project_manager"],
            )

        updated_fields = []
        if project.project_manager_id != users["project_manager"].pk:
            project.project_manager = users["project_manager"]
            updated_fields.append("project_manager")
        if project.status != "active":
            project.status = "active"
            updated_fields.append("status")
        if project.description != "Development-only assessment project for role-based demo access.":
            project.description = "Development-only assessment project for role-based demo access."
            updated_fields.append("description")
        if project.location != "Lagos, Nigeria":
            project.location = "Lagos, Nigeria"
            updated_fields.append("location")
        if updated_fields:
            project.save(update_fields=[*updated_fields, "updated_at"])
        return project

    def _create_or_update_site(self, users: dict[str, User], project: Project) -> Site:
        site = Site.objects.filter(project=project, name=DEMO_SITE_NAME).first()
        if site is None:
            return create_site(
                actor=users["project_manager"],
                project=project,
                name=DEMO_SITE_NAME,
                coordinates=Point(3.4219, 6.4331, srid=4326),
            )

        updated_fields = []
        desired_coordinates = Point(3.4219, 6.4331, srid=4326)
        if site.coordinates != desired_coordinates:
            site.coordinates = desired_coordinates
            updated_fields.append("coordinates")
        if site.coordinate_reference_system != "EPSG:4326":
            site.coordinate_reference_system = "EPSG:4326"
            updated_fields.append("coordinate_reference_system")
        if updated_fields:
            site.save(update_fields=[*updated_fields, "updated_at"])
        return site

    def _ensure_membership(self, *, project: Project, user: User, assigned_by: User) -> None:
        ProjectMembership.objects.get_or_create(
            project=project,
            user=user,
            defaults={"assigned_by": assigned_by},
        )

    def _create_or_update_survey(self, *, users: dict[str, User], project: Project, site: Site) -> Survey:
        survey = Survey.objects.filter(project=project, site=site, name=DEMO_SURVEY_NAME).first()
        if survey is None:
            return create_survey(
                actor=users["survey_engineer"],
                project=project,
                site=site,
                name=DEMO_SURVEY_NAME,
                survey_date=date(2026, 8, 8),
                drone_model="DJI Mavic 3 Enterprise",
                pilot="Demo Survey Engineer",
                notes="Development-only draft survey for role-based assessment walkthroughs.",
            )

        updated_fields = []
        if survey.created_by_id != users["survey_engineer"].pk:
            survey.created_by = users["survey_engineer"]
            updated_fields.append("created_by")
        if survey.status != "DRAFT":
            survey.status = "DRAFT"
            updated_fields.append("status")
        if survey.processing_status != "pending":
            survey.processing_status = "pending"
            updated_fields.append("processing_status")
        if survey.survey_date != date(2026, 8, 8):
            survey.survey_date = date(2026, 8, 8)
            updated_fields.append("survey_date")
        if survey.drone_model != "DJI Mavic 3 Enterprise":
            survey.drone_model = "DJI Mavic 3 Enterprise"
            updated_fields.append("drone_model")
        if survey.pilot != "Demo Survey Engineer":
            survey.pilot = "Demo Survey Engineer"
            updated_fields.append("pilot")
        if survey.notes != "Development-only draft survey for role-based assessment walkthroughs.":
            survey.notes = "Development-only draft survey for role-based assessment walkthroughs."
            updated_fields.append("notes")
        if updated_fields:
            survey.save(update_fields=[*updated_fields, "updated_at"])
        return survey

    def _ensure_demo_measurement(self, *, users: dict[str, User], survey: Survey) -> None:
        if survey.measurements.filter(name="Assessment demo boundary").exists():
            return

        create_measurement(
            actor=users["viewer"],
            survey_id=survey.pk,
            measurement_type=MeasurementType.DISTANCE,
            name="Assessment demo boundary",
            coordinates=[
                [3.4219, 6.4331],
                [3.4227, 6.4338],
            ],
        )
