from datetime import date, timedelta

from rest_framework.test import APITestCase

from apps.audit.models import AuditAction, AuditLog
from apps.projects.models import ProjectMembership
from apps.surveys.models import Survey, SurveyStatus
from apps.surveys.tests_api_support import SurveyApiSupportMixin


class SurveyApiTests(SurveyApiSupportMixin, APITestCase):
    def test_unauthenticated_requests_return_401(self):
        responses = (
            self.client.get(self.list_url),
            self.client.get(f"{self.list_url}/{self.creator_survey.pk}"),
            self.client.post(self.list_url, {}, format="json"),
            self.client.patch(f"{self.list_url}/{self.creator_survey.pk}", {}, format="json"),
        )

        for response in responses:
            self.assertEqual(response.status_code, 401)

    def test_list_and_detail_visibility_match_role_scope(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            admin_list = self.client.get(self.list_url)
            admin_detail = self.client.get(f"{self.list_url}/{self.other_project_survey.pk}")

            self.authenticate(self.owner_manager)
            owner_list = self.client.get(self.list_url)
            owner_detail = self.client.get(f"{self.list_url}/{self.creator_survey.pk}")
            owner_forbidden = self.client.get(f"{self.list_url}/{self.other_project_survey.pk}")

            self.authenticate(self.assigned_engineer)
            engineer_list = self.client.get(self.list_url)
            engineer_detail = self.client.get(f"{self.list_url}/{self.creator_survey.pk}")
            engineer_forbidden = self.client.get(f"{self.list_url}/{self.other_project_survey.pk}")

            self.authenticate(self.assigned_viewer)
            viewer_list = self.client.get(self.list_url)
            viewer_detail = self.client.get(f"{self.list_url}/{self.pm_survey.pk}")

            self.authenticate(self.unassigned_engineer)
            unassigned_engineer_list = self.client.get(self.list_url)
            unassigned_engineer_detail = self.client.get(f"{self.list_url}/{self.creator_survey.pk}")

            self.authenticate(self.unassigned_viewer)
            unassigned_viewer_list = self.client.get(self.list_url)
            unassigned_viewer_detail = self.client.get(f"{self.list_url}/{self.creator_survey.pk}")

        self.assertEqual(admin_list.status_code, 200)
        self.assertEqual(admin_list.json()["count"], 4)
        self.assertEqual(admin_detail.status_code, 200)

        self.assertEqual(owner_list.status_code, 200)
        self.assertEqual(
            [item["id"] for item in owner_list.json()["results"]],
            [self.pm_survey.id, self.creator_survey.id, self.archived_project_survey.id],
        )
        self.assertEqual(owner_detail.status_code, 200)
        self.assertEqual(owner_forbidden.status_code, 403)

        self.assertEqual(
            [item["id"] for item in engineer_list.json()["results"]],
            [self.pm_survey.id, self.creator_survey.id],
        )
        self.assertEqual(engineer_detail.status_code, 200)
        self.assertEqual(engineer_forbidden.status_code, 403)

        self.assertEqual(
            [item["id"] for item in viewer_list.json()["results"]],
            [self.pm_survey.id, self.creator_survey.id],
        )
        self.assertEqual(viewer_detail.status_code, 200)

        self.assertEqual(unassigned_engineer_list.json()["count"], 0)
        self.assertEqual(unassigned_engineer_detail.status_code, 403)
        self.assertEqual(unassigned_viewer_list.json()["count"], 1)
        self.assertEqual(unassigned_viewer_detail.status_code, 403)

    def test_detail_representation_uses_expected_fields_and_uppercase_status(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager)
            response = self.client.get(f"{self.list_url}/{self.pm_survey.pk}")

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(body.keys()),
            [
                "id",
                "project_id",
                "site_id",
                "name",
                "survey_date",
                "drone_model",
                "pilot",
                "coordinate_reference_system",
                "status",
                "processing_status",
                "notes",
                "created_by",
                "approved_by",
                "rejection_reason",
                "created_at",
                "updated_at",
            ],
        )
        self.assertEqual(body["status"], SurveyStatus.READY)
        self.assertEqual(body["processing_status"], "completed")
        self.assertEqual(body["rejection_reason"], "Cloud cover obscured the orthomosaic.")

    def test_create_permissions_project_site_mismatch_and_archived_project_validation(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            admin_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Admin Created Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )
            mismatch_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.other_site.pk,
                    "name": "Mismatch Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )
            archived_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.archived_project.pk,
                    "site_id": self.archived_site.pk,
                    "name": "Archived Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )

            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            pm_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.second_site.pk,
                    "name": "PM Created Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )

            self.authenticate(self.assigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            engineer_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Engineer Created Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )

            self.authenticate(self.assigned_viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Viewer Blocked Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )

            self.authenticate(self.unassigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            unassigned_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Unassigned Blocked Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )

        self.assertEqual(admin_response.status_code, 201)
        self.assertEqual(pm_response.status_code, 201)
        self.assertEqual(engineer_response.status_code, 201)
        self.assertEqual(viewer_response.status_code, 403)
        self.assertEqual(unassigned_response.status_code, 403)
        self.assertEqual(mismatch_response.status_code, 400)
        self.assertEqual(archived_response.status_code, 400)
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("action", flat=True)),
            [
                AuditAction.SURVEY_CREATED,
                AuditAction.SURVEY_CREATED,
                AuditAction.SURVEY_CREATED,
            ],
        )

    def test_survey_engineer_update_requires_creator_ownership_and_current_membership(self):
        with self.auth_settings():
            self.authenticate(self.assigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            creator_update = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"notes": "Engineer updated notes."},
                format="json",
            )

            self.authenticate(self.other_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            non_creator_update = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"notes": "Blocked note"},
                format="json",
            )

            ProjectMembership.objects.filter(
                project=self.project,
                user=self.assigned_engineer,
            ).delete()
            self.authenticate(self.assigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()
            lost_membership_update = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"name": "Blocked After Membership Removal"},
                format="json",
            )

        self.creator_survey.refresh_from_db()
        self.assertEqual(creator_update.status_code, 200)
        self.assertEqual(non_creator_update.status_code, 403)
        self.assertEqual(lost_membership_update.status_code, 403)
        self.assertEqual(self.creator_survey.notes, "Engineer updated notes.")
        self.assertEqual(
            list(AuditLog.objects.values_list("action", flat=True)),
            [AuditAction.SURVEY_UPDATED],
        )

    def test_viewer_is_read_only_for_patch(self):
        with self.auth_settings():
            self.authenticate(self.assigned_viewer, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"notes": "Blocked viewer update"},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.creator_survey.refresh_from_db()
        self.assertEqual(self.creator_survey.notes, "Captured after rainfall.")

    def test_create_and_patch_payload_validation_is_strict(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            missing_required = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Incomplete Survey",
                },
                format="json",
            )
            unknown_create_field = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Unknown Field Survey",
                    "survey_date": "2026-08-09",
                    "status": "APPROVED",
                },
                format="json",
            )
            invalid_create_date = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Bad Date Survey",
                    "survey_date": "2026-99-99",
                },
                format="json",
            )
            form_unknown_patch = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {
                    "notes": "Allowed note",
                    "rejection_reason": "not writable",
                },
            )
            invalid_patch_date = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"survey_date": "not-a-date"},
                format="json",
            )

        self.assertEqual(missing_required.status_code, 400)
        self.assertEqual(unknown_create_field.status_code, 400)
        self.assertEqual(invalid_create_date.status_code, 400)
        self.assertEqual(form_unknown_patch.status_code, 400)
        self.assertEqual(invalid_patch_date.status_code, 400)

    def test_collection_filters_sorting_and_pagination(self):
        older_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Older Ready Survey",
            survey_date=date(2026, 8, 1),
            status=SurveyStatus.READY,
            processing_status="completed",
            created_by=self.owner_manager,
        )
        newest_survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Newest Ready Survey",
            survey_date=date(2026, 8, 9),
            status=SurveyStatus.READY,
            processing_status="completed",
            created_by=self.owner_manager,
        )
        for index in range(25):
            Survey.objects.create(
                project=self.project,
                site=self.site,
                name=f"Paged Survey {index}",
                survey_date=date(2026, 7, 1) + timedelta(days=index),
                created_by=self.owner_manager,
            )

        with self.auth_settings():
            self.authenticate(self.admin)
            status_filter = self.client.get(f"{self.list_url}?status=READY")
            project_filter = self.client.get(f"{self.list_url}?project_id={self.project.pk}")
            site_filter = self.client.get(f"{self.list_url}?site_id={self.second_site.pk}")
            from_date_filter = self.client.get(f"{self.list_url}?from_date=2026-08-06")
            sorted_asc = self.client.get(f"{self.list_url}?sort=survey_date&order=asc")
            sorted_desc = self.client.get(f"{self.list_url}?sort=survey_date&order=desc")
            paged = self.client.get(f"{self.list_url}?limit=5&offset=10")
            invalid_status = self.client.get(f"{self.list_url}?status=ready")
            invalid_filter = self.client.get(f"{self.list_url}?survey_id=1")
            invalid_sort = self.client.get(f"{self.list_url}?sort=name&order=asc")
            invalid_sort_pair = self.client.get(f"{self.list_url}?sort=survey_date")

        self.assertEqual(status_filter.status_code, 200)
        self.assertEqual(
            [item["id"] for item in status_filter.json()["results"][:3]],
            [newest_survey.id, self.pm_survey.id, older_survey.id],
        )
        self.assertEqual(project_filter.json()["count"], 29)
        self.assertEqual(site_filter.json()["count"], 1)
        self.assertEqual(
            [item["id"] for item in from_date_filter.json()["results"][:3]],
            [newest_survey.id, self.other_project_survey.id, self.pm_survey.id],
        )
        asc_dates = [item["survey_date"] for item in sorted_asc.json()["results"][:5]]
        desc_dates = [item["survey_date"] for item in sorted_desc.json()["results"][:5]]
        self.assertEqual(asc_dates, sorted(asc_dates))
        self.assertEqual(desc_dates, sorted(desc_dates, reverse=True))
        self.assertIn(older_survey.id, [item["id"] for item in status_filter.json()["results"]])
        self.assertEqual(desc_ids := [item["id"] for item in sorted_desc.json()["results"][:5]][0], newest_survey.id)
        self.assertEqual(paged.status_code, 200)
        self.assertEqual(len(paged.json()["results"]), 5)
        self.assertEqual(paged.json()["count"], 31)
        self.assertEqual(invalid_status.status_code, 400)
        self.assertEqual(invalid_filter.status_code, 400)
        self.assertEqual(invalid_sort.status_code, 400)
        self.assertEqual(invalid_sort_pair.status_code, 400)

    def test_unsafe_requests_require_csrf_and_succeed_with_valid_token(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            missing_csrf = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Blocked Without CSRF",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            token = self.add_csrf()
            allowed_post = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Allowed With CSRF",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )
            allowed_patch = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"pilot": "Updated Pilot"},
                format="json",
            )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed_post.status_code, 201)
        self.assertEqual(allowed_patch.status_code, 200)
        self.assertTrue(token)

    def test_create_and_update_write_expected_audit_events(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            create_response = self.client.post(
                self.list_url,
                {
                    "project_id": self.project.pk,
                    "site_id": self.site.pk,
                    "name": "Audited Survey",
                    "survey_date": "2026-08-09",
                },
                format="json",
            )
            patch_response = self.client.patch(
                f"{self.list_url}/{self.creator_survey.pk}",
                {"notes": "Manager updated notes."},
                format="json",
            )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("action", flat=True)),
            [AuditAction.SURVEY_CREATED, AuditAction.SURVEY_UPDATED],
        )
