from datetime import datetime, timedelta, timezone

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.contrib.gis.geos import Point
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.projects.models import Project, ProjectMembership, Site


class ProjectSiteApiTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        cls.public_key_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def setUp(self):
        self.admin = self.create_user("admin@example.com", "admin-1", UserRole.ADMINISTRATOR)
        self.owner_manager = self.create_user(
            "owner-manager@example.com",
            "manager-1",
            UserRole.PROJECT_MANAGER,
        )
        self.other_manager = self.create_user(
            "other-manager@example.com",
            "manager-2",
            UserRole.PROJECT_MANAGER,
        )
        self.new_manager = self.create_user(
            "new-manager@example.com",
            "manager-3",
            UserRole.PROJECT_MANAGER,
        )
        self.inactive_manager = self.create_user(
            "inactive-manager@example.com",
            "manager-4",
            UserRole.PROJECT_MANAGER,
            is_active=False,
        )
        self.assigned_engineer = self.create_user(
            "assigned-engineer@example.com",
            "engineer-1",
            UserRole.SURVEY_ENGINEER,
        )
        self.unassigned_engineer = self.create_user(
            "unassigned-engineer@example.com",
            "engineer-2",
            UserRole.SURVEY_ENGINEER,
        )
        self.assigned_viewer = self.create_user(
            "assigned-viewer@example.com",
            "viewer-1",
            UserRole.VIEWER,
        )
        self.unassigned_viewer = self.create_user(
            "unassigned-viewer@example.com",
            "viewer-2",
            UserRole.VIEWER,
        )

        self.project = Project.objects.create(
            name="Victoria Island Survey",
            description="Primary project",
            location="Lagos",
            status="active",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )
        self.other_project = Project.objects.create(
            name="Abuja Mapping",
            description="Other project",
            location="Abuja",
            status="active",
            project_manager=self.other_manager,
            created_by=self.admin,
        )
        self.archived_project = Project.objects.create(
            name="Archived Project",
            status="archived",
            project_manager=self.owner_manager,
            created_by=self.admin,
        )

        ProjectMembership.objects.create(
            project=self.project,
            user=self.assigned_engineer,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.assigned_viewer,
            assigned_by=self.owner_manager,
        )
        ProjectMembership.objects.create(
            project=self.other_project,
            user=self.unassigned_viewer,
            assigned_by=self.other_manager,
        )

        self.site = Site.objects.create(
            project=self.project,
            name="Existing Site",
            coordinates=Point(3.4723, 6.4281, srid=4326),
        )
        self.other_site = Site.objects.create(
            project=self.other_project,
            name="Other Site",
            coordinates=Point(7.4913, 9.0579, srid=4326),
        )
        self.archived_site = Site.objects.create(
            project=self.archived_project,
            name="Archived Site",
            coordinates=Point(3.5, 6.5, srid=4326),
        )

        self.projects_url = "/api/v1/projects"

    def auth_settings(self):
        return override_settings(
            HITECH_AUTH_JWT_PUBLIC_KEY=self.public_key_pem,
            HITECH_AUTH_ACCESS_COOKIE_NAME="hitech_access_token",
        )

    def create_user(self, email, external_id, role, **extra_fields):
        return User.objects.create_user(
            email=email,
            external_id=external_id,
            role=role,
            **extra_fields,
        )

    def make_token(self, user):
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user.external_id,
                "email": user.email,
                "role": user.role,
                "exp": now + timedelta(minutes=15),
            },
            self.private_key_pem,
            algorithm="RS256",
        )

    def authenticate(self, user, *, enforce_csrf_checks=False):
        self.client = self.client_class(enforce_csrf_checks=enforce_csrf_checks)
        self.client.cookies[settings.HITECH_AUTH_ACCESS_COOKIE_NAME] = self.make_token(user)

    def add_csrf(self, path="/projects"):
        response = self.client.get(path)
        token = response.cookies["csrftoken"].value
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        return token

    def test_unauthenticated_requests_return_401(self):
        with self.auth_settings():
            responses = (
                self.client.get(self.projects_url),
                self.client.get(f"{self.projects_url}/{self.project.pk}"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/members"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/available-members"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/sites"),
                self.client.get(f"{self.projects_url}/{self.project.pk}/sites/{self.site.pk}"),
            )

        for response in responses:
            self.assertEqual(response.status_code, 401)

    def test_project_membership_endpoints_enforce_owner_admin_scope(self):
        with self.auth_settings():
            self.authenticate(self.assigned_viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_members = self.client.get(f"{self.projects_url}/{self.project.pk}/members")
            viewer_candidates = self.client.get(
                f"{self.projects_url}/{self.project.pk}/available-members"
            )
            viewer_add = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )

            self.authenticate(self.other_manager, enforce_csrf_checks=True)
            self.add_csrf()
            other_manager_remove = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.assigned_engineer.pk}"
            )

        for response in (viewer_members, viewer_candidates, viewer_add, other_manager_remove):
            self.assertEqual(response.status_code, 403)

    def test_admin_and_owner_can_list_members_and_available_candidates(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            admin_members = self.client.get(f"{self.projects_url}/{self.project.pk}/members")
            admin_candidates = self.client.get(
                f"{self.projects_url}/{self.project.pk}/available-members"
            )

            self.authenticate(self.owner_manager)
            owner_members = self.client.get(f"{self.projects_url}/{self.project.pk}/members")
            owner_candidates = self.client.get(
                f"{self.projects_url}/{self.project.pk}/available-members"
            )

        for response in (admin_members, admin_candidates, owner_members, owner_candidates):
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            admin_members.json(),
            [
                {
                    "id": self.assigned_engineer.pk,
                    "email": self.assigned_engineer.email,
                    "role": UserRole.SURVEY_ENGINEER,
                },
                {
                    "id": self.assigned_viewer.pk,
                    "email": self.assigned_viewer.email,
                    "role": UserRole.VIEWER,
                },
            ],
        )
        self.assertEqual(owner_members.json(), admin_members.json())
        self.assertEqual(
            admin_candidates.json(),
            [
                {
                    "id": self.unassigned_engineer.pk,
                    "email": self.unassigned_engineer.email,
                    "role": UserRole.SURVEY_ENGINEER,
                }
            ],
        )
        self.assertEqual(owner_candidates.json(), admin_candidates.json())

    def test_membership_add_remove_validation_archived_constraint_and_audit(self):
        inactive_viewer = self.create_user(
            "inactive-viewer@example.com",
            "viewer-3",
            UserRole.VIEWER,
            is_active=False,
        )

        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            added = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )
            duplicate = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )
            invalid_role = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.new_manager.pk},
                format="json",
            )
            inactive_target = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": inactive_viewer.pk},
                format="json",
            )
            removed = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.unassigned_engineer.pk}"
            )
            remove_missing = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.unassigned_engineer.pk}"
            )
            archived_members = self.client.get(
                f"{self.projects_url}/{self.archived_project.pk}/members"
            )
            archived_candidates = self.client.get(
                f"{self.projects_url}/{self.archived_project.pk}/available-members"
            )
            archived_add = self.client.post(
                f"{self.projects_url}/{self.archived_project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )

        self.assertEqual(added.status_code, 201)
        self.assertEqual(
            added.json(),
            {
                "id": self.unassigned_engineer.pk,
                "email": self.unassigned_engineer.email,
                "role": UserRole.SURVEY_ENGINEER,
            },
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(invalid_role.status_code, 400)
        self.assertEqual(inactive_target.status_code, 400)
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(remove_missing.status_code, 400)
        self.assertEqual(archived_members.status_code, 400)
        self.assertEqual(archived_candidates.status_code, 400)
        self.assertEqual(archived_add.status_code, 400)
        self.assertFalse(
            ProjectMembership.objects.filter(project=self.project, user=self.unassigned_engineer).exists()
        )
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("details", flat=True)),
            [
                {"operation": "added", "member_id": self.unassigned_engineer.pk},
                {"operation": "removed", "member_id": self.unassigned_engineer.pk},
            ],
        )

    def test_membership_mutations_require_valid_csrf(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            missing_csrf = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            token = self.add_csrf()
            allowed_post = self.client.post(
                f"{self.projects_url}/{self.project.pk}/members",
                {"user_id": self.unassigned_engineer.pk},
                format="json",
            )
            allowed_delete = self.client.delete(
                f"{self.projects_url}/{self.project.pk}/members/{self.unassigned_engineer.pk}"
            )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed_post.status_code, 201)
        self.assertEqual(allowed_delete.status_code, 204)
        self.assertTrue(token)

    def test_project_list_filters_by_role_and_assignment(self):
        with self.auth_settings():
            self.authenticate(self.admin)
            admin_response = self.client.get(self.projects_url)

            self.authenticate(self.owner_manager)
            owner_response = self.client.get(self.projects_url)

            self.authenticate(self.assigned_engineer)
            assigned_engineer_response = self.client.get(self.projects_url)

            self.authenticate(self.assigned_viewer)
            assigned_viewer_response = self.client.get(self.projects_url)

            self.authenticate(self.unassigned_engineer)
            unassigned_engineer_response = self.client.get(self.projects_url)

        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json()["count"], 3)

        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(
            [item["id"] for item in owner_response.json()["results"]],
            [self.project.id, self.archived_project.id],
        )
        self.assertEqual(
            [item["id"] for item in assigned_engineer_response.json()["results"]],
            [self.project.id],
        )
        self.assertEqual(
            [item["id"] for item in assigned_viewer_response.json()["results"]],
            [self.project.id],
        )
        self.assertEqual(unassigned_engineer_response.json()["count"], 0)

    def test_forbidden_cross_project_reads_and_writes_return_403(self):
        with self.auth_settings():
            self.authenticate(self.assigned_engineer, enforce_csrf_checks=True)
            self.add_csrf()

            forbidden_project_read = self.client.get(f"{self.projects_url}/{self.other_project.pk}")
            forbidden_site_read = self.client.get(
                f"{self.projects_url}/{self.project.pk}/sites/{self.other_site.pk}"
            )
            forbidden_project_write = self.client.patch(
                f"{self.projects_url}/{self.other_project.pk}",
                {"name": "Blocked"},
                format="json",
            )
            forbidden_site_write = self.client.patch(
                f"{self.projects_url}/{self.other_project.pk}/sites/{self.other_site.pk}",
                {"name": "Blocked"},
                format="json",
            )

        for response in (
            forbidden_project_read,
            forbidden_site_read,
            forbidden_project_write,
            forbidden_site_write,
        ):
            self.assertEqual(response.status_code, 403)

    def test_administrator_can_create_project_and_audit_is_written(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.projects_url,
                {
                    "name": "Lekki Phase 1 Road Expansion",
                    "description": "Dualisation",
                    "location": "Lekki, Lagos",
                    "project_manager_id": self.new_manager.pk,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["project_manager_id"], self.new_manager.pk)
        self.assertEqual(body["created_by"], self.admin.pk)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(AuditLog.objects.get().action, AuditAction.PROJECT_CREATED)

    def test_project_manager_can_create_project_and_is_assigned_as_manager(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.projects_url,
                {"name": "PM Created Project"},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["project_manager_id"], self.owner_manager.pk)

    def test_administrator_project_manager_assignment_validation(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            missing_manager = self.client.post(
                self.projects_url,
                {"name": "Missing Manager"},
                format="json",
            )
            inactive_manager = self.client.post(
                self.projects_url,
                {
                    "name": "Inactive Manager",
                    "project_manager_id": self.inactive_manager.pk,
                },
                format="json",
            )

        self.assertEqual(missing_manager.status_code, 400)
        self.assertEqual(inactive_manager.status_code, 400)

    def test_create_payloads_require_documented_fields(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            missing_project_name = self.client.post(
                self.projects_url,
                {"project_manager_id": self.new_manager.pk},
                format="json",
            )
            missing_site_name = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {"coordinates": {"lat": 6.4, "lng": 3.4}},
                format="json",
            )
            missing_site_coordinates = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {"name": "Incomplete Site"},
                format="json",
            )

        self.assertEqual(missing_project_name.status_code, 400)
        self.assertEqual(missing_site_name.status_code, 400)
        self.assertEqual(missing_site_coordinates.status_code, 400)

    def test_unexpected_write_field_is_rejected_with_400(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            project_response = self.client.post(
                self.projects_url,
                {
                    "name": "Unexpected Field Project",
                    "project_manager_id": self.new_manager.pk,
                    "status": "archived",
                },
                format="json",
            )
            site_response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}/sites/{self.site.pk}",
                {"project_id": self.other_project.pk},
                format="json",
            )

        self.assertEqual(project_response.status_code, 400)
        self.assertEqual(site_response.status_code, 400)

    def test_invalid_coordinate_ranges_and_conflicting_crs_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            invalid_latitude = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Invalid Latitude",
                    "coordinates": {"lat": 91, "lng": 3.4},
                },
                format="json",
            )
            invalid_longitude = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Invalid Longitude",
                    "coordinates": {"lat": 6.4, "lng": 181},
                },
                format="json",
            )
            conflicting_crs = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Invalid CRS",
                    "coordinates": {"lat": 6.4, "lng": 3.4},
                    "coordinate_reference_system": "EPSG:3857",
                },
                format="json",
            )

        self.assertEqual(invalid_latitude.status_code, 400)
        self.assertEqual(invalid_longitude.status_code, 400)
        self.assertEqual(conflicting_crs.status_code, 400)

    def test_string_coordinate_values_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "String Coordinates",
                    "coordinates": {"lat": "6.4", "lng": "3.4"},
                },
                format="json",
            )

        self.assertEqual(response.status_code, 400)

    def test_non_finite_coordinate_values_are_rejected(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                '{"name":"Non Finite Coordinates","coordinates":{"lat":NaN,"lng":3.4}}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)

    def test_unknown_write_field_is_rejected_for_form_style_request_data(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.post(
                self.projects_url,
                {
                    "name": "Form Project",
                    "project_manager_id": str(self.new_manager.pk),
                    "unexpected": "value",
                },
            )

        self.assertEqual(response.status_code, 400)

    def test_owner_can_update_and_archive_without_hard_delete(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            update_response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}",
                {"name": "Updated Project Name"},
                format="json",
            )
            archive_response = self.client.delete(f"{self.projects_url}/{self.project.pk}")

        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(archive_response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, "archived")
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())
        self.assertEqual(
            list(AuditLog.objects.order_by("id").values_list("action", flat=True)),
            [AuditAction.PROJECT_UPDATED, AuditAction.PROJECT_ARCHIVED],
        )

    def test_project_manager_reassignment_attempt_is_rejected(self):
        with self.auth_settings():
            self.authenticate(self.owner_manager, enforce_csrf_checks=True)
            self.add_csrf()
            response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}",
                {"project_manager_id": self.new_manager.pk},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.project_manager, self.owner_manager)

    def test_site_crud_permissions_and_coordinate_representation(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            create_response = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {
                    "name": "Created Site",
                    "coordinates": {"lat": 6.5001, "lng": 3.6002},
                    "coordinate_reference_system": "EPSG:4326",
                },
                format="json",
            )

            site_id = create_response.json()["id"]
            read_response = self.client.get(f"{self.projects_url}/{self.project.pk}/sites/{site_id}")
            update_response = self.client.patch(
                f"{self.projects_url}/{self.project.pk}/sites/{site_id}",
                {"coordinates": {"lat": 6.7001, "lng": 3.8002}},
                format="json",
            )
            updated_site = Site.objects.get(pk=site_id)
            delete_response = self.client.delete(f"{self.projects_url}/{self.project.pk}/sites/{site_id}")

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 204)

        self.assertAlmostEqual(updated_site.coordinates.x, 3.8002)
        self.assertAlmostEqual(updated_site.coordinates.y, 6.7001)
        self.assertEqual(read_response.json()["coordinates"], {"lat": 6.5001, "lng": 3.6002})
        self.assertFalse(Site.objects.filter(pk=site_id).exists())

    def test_site_read_only_users_and_archived_project_write_rules(self):
        with self.auth_settings():
            self.authenticate(self.assigned_viewer, enforce_csrf_checks=True)
            self.add_csrf()
            viewer_post = self.client.post(
                f"{self.projects_url}/{self.project.pk}/sites",
                {"name": "Blocked", "coordinates": {"lat": 6.4, "lng": 3.4}},
                format="json",
            )
            viewer_patch = self.client.patch(
                f"{self.projects_url}/{self.project.pk}/sites/{self.site.pk}",
                {"name": "Blocked"},
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            self.add_csrf()
            archived_post = self.client.post(
                f"{self.projects_url}/{self.archived_project.pk}/sites",
                {"name": "Blocked", "coordinates": {"lat": 6.4, "lng": 3.4}},
                format="json",
            )

        self.assertEqual(viewer_post.status_code, 403)
        self.assertEqual(viewer_patch.status_code, 403)
        self.assertEqual(archived_post.status_code, 400)

    def test_limit_offset_pagination_on_collections(self):
        for index in range(25):
            Project.objects.create(
                name=f"Admin Project {index}",
                project_manager=self.other_manager,
                created_by=self.admin,
            )

        for index in range(25):
            Site.objects.create(
                project=self.project,
                name=f"Site {index}",
                coordinates=Point(3.0 + index / 1000, 6.0 + index / 1000, srid=4326),
            )

        with self.auth_settings():
            self.authenticate(self.admin)
            project_response = self.client.get(f"{self.projects_url}?limit=5&offset=10")
            site_response = self.client.get(
                f"{self.projects_url}/{self.project.pk}/sites?limit=5&offset=10"
            )

        self.assertEqual(project_response.status_code, 200)
        self.assertEqual(len(project_response.json()["results"]), 5)
        self.assertIn("next", project_response.json())
        self.assertEqual(site_response.status_code, 200)
        self.assertEqual(len(site_response.json()["results"]), 5)

    def test_unsafe_authenticated_requests_require_valid_csrf_token(self):
        with self.auth_settings():
            self.authenticate(self.admin, enforce_csrf_checks=True)
            missing_csrf = self.client.post(
                self.projects_url,
                {
                    "name": "Blocked Without CSRF",
                    "project_manager_id": self.new_manager.pk,
                },
                format="json",
            )

            self.authenticate(self.admin, enforce_csrf_checks=True)
            token = self.add_csrf()
            allowed = self.client.post(
                self.projects_url,
                {
                    "name": "Allowed With CSRF",
                    "project_manager_id": self.new_manager.pk,
                },
                format="json",
            )

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(allowed.status_code, 201)
        self.assertTrue(token)

    def test_template_pages_issue_csrf_cookie(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
