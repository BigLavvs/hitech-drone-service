from __future__ import annotations

from datetime import date

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access_control.demo_access import DEMO_USER_SPECS, ensure_demo_auth_enabled
from apps.access_control.services import upsert_demo_user
from apps.maps.models import MeasurementType
from apps.maps.services import create_measurement, measurement_exists_for_survey
from apps.projects.services import (
    create_project,
    create_site,
    ensure_project_membership,
    get_project_for_creator_and_name,
    get_site_for_project_and_name,
    synchronize_demo_project,
    update_site,
)
from apps.surveys.services import (
    create_survey,
    get_survey_for_project_site_and_name,
    synchronize_demo_survey,
)

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
        return upsert_demo_user(spec=spec)

    def _create_or_update_project(self, users):
        project = get_project_for_creator_and_name(
            creator=users["administrator"],
            name=DEMO_PROJECT_NAME,
        )
        if project is None:
            return create_project(
                actor=users["administrator"],
                name=DEMO_PROJECT_NAME,
                description="Development-only assessment project for role-based demo access.",
                location="Lagos, Nigeria",
                project_manager=users["project_manager"],
            )

        return synchronize_demo_project(
            actor=users["administrator"],
            project=project,
            project_manager=users["project_manager"],
        )

    def _create_or_update_site(self, users, project):
        site = get_site_for_project_and_name(project=project, name=DEMO_SITE_NAME)
        if site is None:
            return create_site(
                actor=users["project_manager"],
                project=project,
                name=DEMO_SITE_NAME,
                coordinates=Point(3.4219, 6.4331, srid=4326),
            )

        desired_coordinates = Point(3.4219, 6.4331, srid=4326)
        return update_site(
            actor=users["project_manager"],
            site=site,
            coordinates=desired_coordinates,
            coordinate_reference_system="EPSG:4326",
        )

    def _ensure_membership(self, *, project: Project, user: User, assigned_by: User) -> None:
        ensure_project_membership(project=project, user=user, assigned_by=assigned_by)

    def _create_or_update_survey(self, *, users, project, site):
        survey = get_survey_for_project_site_and_name(
            project=project,
            site=site,
            name=DEMO_SURVEY_NAME,
        )
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

        return synchronize_demo_survey(
            survey=survey,
            creator=users["survey_engineer"],
            survey_date=date(2026, 8, 8),
        )

    def _ensure_demo_measurement(self, *, users, survey) -> None:
        if measurement_exists_for_survey(survey=survey, name="Assessment demo boundary"):
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
