from .support import *


class ProjectModelsTestCase(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            email="manager@example.com",
            external_id="manager-1",
            role=UserRole.PROJECT_MANAGER,
        )
        self.creator = User.objects.create_user(
            email="creator@example.com",
            external_id="creator-1",
            role=UserRole.ADMINISTRATOR,
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            external_id="member-1",
            role=UserRole.SURVEY_ENGINEER,
        )

    def test_project_defaults_and_user_relationships(self):
        project = Project.objects.create(
            name="Lekki Site Survey",
            project_manager=self.manager,
            created_by=self.creator,
        )

        self.assertEqual(project.status, "active")
        self.assertEqual(project.project_manager, self.manager)
        self.assertEqual(project.created_by, self.creator)
        self.assertEqual(self.manager.projects_owned.get(), project)
        self.assertEqual(self.creator.projects_created.get(), project)

    def test_project_membership_duplicate_project_user_pair_is_rejected(self):
        project = Project.objects.create(name="Eko Atlantic Mapping")
        ProjectMembership.objects.create(project=project, user=self.member, assigned_by=self.manager)

        with self.assertRaises(IntegrityError):
            ProjectMembership.objects.create(project=project, user=self.member)

    def test_deleting_project_cascades_to_memberships_and_sites(self):
        project = Project.objects.create(name="Abuja Corridor Scan")
        membership = ProjectMembership.objects.create(
            project=project,
            user=self.member,
            assigned_by=self.manager,
        )
        site = Site.objects.create(
            project=project,
            name="Block A",
            coordinates=Point(3.4723, 6.4281, srid=4326),
        )

        project.delete()

        self.assertFalse(ProjectMembership.objects.filter(pk=membership.pk).exists())
        self.assertFalse(Site.objects.filter(pk=site.pk).exists())

    def test_site_coordinates_persist_as_point_with_srid_4326(self):
        project = Project.objects.create(name="Port Harcourt Survey")
        site = Site.objects.create(
            project=project,
            name="Jetty",
            coordinates=Point(7.0134, 4.8156, srid=4326),
        )

        site.refresh_from_db()

        self.assertIsInstance(site.coordinates, Point)
        self.assertEqual(site.coordinates.srid, 4326)
        self.assertAlmostEqual(site.coordinates.x, 7.0134)
        self.assertAlmostEqual(site.coordinates.y, 4.8156)
