from django.conf import settings
from django.contrib.gis.db import models


class Project(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=50, default="active")
    project_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="projects_owned",
        db_column="project_manager_id",
        blank=True,
        null=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="projects_created",
        db_column="created_by_id",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project"
        indexes = [
            models.Index(fields=["project_manager"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.name


class ProjectMembership(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="memberships",
        db_column="project_id",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects_assigned",
        db_column="user_id",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="memberships_assigned",
        db_column="assigned_by_id",
        blank=True,
        null=True,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_membership"
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="project_membership_unique"),
        ]
        indexes = [
            models.Index(fields=["project"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.project_id}"


class Site(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sites",
        db_column="project_id",
    )
    name = models.CharField(max_length=255)
    coordinates = models.PointField(srid=4326)
    coordinate_reference_system = models.CharField(
        max_length=50,
        default="EPSG:4326",
        db_column="coordinate_reference_system",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "site"
        indexes = [
            models.Index(fields=["project"]),
        ]

    def __str__(self) -> str:
        return self.name
