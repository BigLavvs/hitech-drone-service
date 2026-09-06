from .support import *
from .test_authorization_csrf import ProjectApiAuthorizationCsrfMixin
from .test_membership_api import ProjectApiMembershipMixin
from .test_pagination import ProjectApiPaginationMixin
from .test_project_api import ProjectApiProjectMixin
from .test_site_api import ProjectApiSiteMixin


class ProjectSiteApiTests(ProjectApiAuthorizationCsrfMixin, ProjectApiProjectMixin, ProjectApiMembershipMixin, ProjectApiSiteMixin, ProjectApiPaginationMixin, APITestCase):
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
