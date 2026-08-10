from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.models import Project, ProjectMembership, Site

_UNSET = object()


def get_projects_visible_to_user(*, user: User):
    queryset = Project.objects.select_related("project_manager", "created_by").order_by("id")

    if not user.is_active:
        return queryset.none()

    if user.role == UserRole.ADMINISTRATOR:
        return queryset

    if user.role == UserRole.PROJECT_MANAGER:
        return queryset.filter(project_manager=user)

    if user.role in {UserRole.SURVEY_ENGINEER, UserRole.VIEWER}:
        return queryset.filter(memberships__user=user).distinct()

    return queryset.none()


def get_project_visible_to_user(*, user: User, project_id: int) -> Project:
    project = (
        Project.objects.select_related("project_manager", "created_by")
        .filter(pk=project_id)
        .first()
    )
    if project is None:
        raise Project.DoesNotExist

    if not user_can_view_project(user, project):
        raise PermissionDenied("You do not have permission to access this project.")

    return project


def get_project_manageable_by_user(*, user: User, project_id: int) -> Project:
    project = (
        Project.objects.select_related("project_manager", "created_by")
        .filter(pk=project_id)
        .first()
    )
    if project is None:
        raise Project.DoesNotExist

    if not user_can_manage_project(user, project):
        raise PermissionDenied("You do not have permission to modify this project.")

    return project


def get_site_with_project(*, site_id: int) -> Site:
    site = Site.objects.select_related("project").filter(pk=site_id).first()
    if site is None:
        raise Site.DoesNotExist
    return site


def user_can_view_project(user: User, project: Project) -> bool:
    if not user.is_active:
        return False

    if user.role == UserRole.ADMINISTRATOR:
        return True

    if user.role == UserRole.PROJECT_MANAGER:
        return project.project_manager_id == user.id

    if user.role in {UserRole.SURVEY_ENGINEER, UserRole.VIEWER}:
        return _user_has_project_membership(user=user, project=project)

    return False


def user_can_manage_project(user: User, project: Project) -> bool:
    if not user.is_active:
        return False

    if user.role == UserRole.ADMINISTRATOR:
        return True

    if user.role == UserRole.PROJECT_MANAGER:
        return project.project_manager_id == user.id

    return False


def create_project(
    *,
    actor: User,
    name: str,
    description: str | None = None,
    location: str | None = None,
    project_manager: User | None = None,
) -> Project:
    if not actor.is_active:
        raise PermissionDenied("Only active administrators and project managers can create projects.")

    if actor.role == UserRole.ADMINISTRATOR:
        assigned_project_manager = project_manager
        if assigned_project_manager is None:
            raise ValidationError("Administrators must assign a project manager when creating a project.")
    elif actor.role == UserRole.PROJECT_MANAGER:
        assigned_project_manager = actor
    else:
        raise PermissionDenied("Only active administrators and project managers can create projects.")

    _validate_project_manager(assigned_project_manager)

    with transaction.atomic():
        project = Project.objects.create(
            name=name,
            description=description,
            location=location,
            project_manager=assigned_project_manager,
            created_by=actor,
        )
        record_audit_event(
            action=AuditAction.PROJECT_CREATED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=project,
        )

    return project


def update_project(
    *,
    actor: User,
    project: Project,
    name: str | object = _UNSET,
    description: str | None | object = _UNSET,
    location: str | None | object = _UNSET,
    project_manager: User | object = _UNSET,
) -> Project:
    if not user_can_manage_project(actor, project):
        raise PermissionDenied("Only active administrators and the owning project manager can update a project.")

    update_fields: list[str] = []

    if name is not _UNSET:
        project.name = name
        update_fields.append("name")

    if description is not _UNSET:
        project.description = description
        update_fields.append("description")

    if location is not _UNSET:
        project.location = location
        update_fields.append("location")

    if project_manager is not _UNSET:
        _validate_project_manager(project_manager)
        project.project_manager = project_manager
        update_fields.append("project_manager")

    if not update_fields:
        return project

    with transaction.atomic():
        project.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=AuditAction.PROJECT_UPDATED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=project,
        )

    return project


def archive_project(*, actor: User, project: Project) -> Project:
    if not user_can_manage_project(actor, project):
        raise PermissionDenied(
            "Only active administrators and the owning project manager can archive a project."
        )

    if project.status != "active":
        raise ValidationError("Only active projects can be archived.")

    with transaction.atomic():
        project.status = "archived"
        project.save(update_fields=["status", "updated_at"])
        record_audit_event(
            action=AuditAction.PROJECT_ARCHIVED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=project,
        )

    return project


def add_project_member(*, actor: User, project: Project, member: User) -> ProjectMembership:
    _validate_project_membership_actor(actor=actor, project=project)
    _validate_project_membership_target(member)

    if ProjectMembership.objects.filter(project=project, user=member).exists():
        raise ValidationError("User is already a member of this project.")

    with transaction.atomic():
        membership = ProjectMembership.objects.create(
            project=project,
            user=member,
            assigned_by=actor,
        )
        record_audit_event(
            action=AuditAction.PROJECT_UPDATED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=project,
            details={"operation": "added", "member_id": member.pk},
        )

    return membership


def remove_project_member(*, actor: User, project: Project, member: User) -> None:
    _validate_project_membership_actor(actor=actor, project=project)
    _validate_project_membership_target(member)

    try:
        membership = ProjectMembership.objects.get(project=project, user=member)
    except ProjectMembership.DoesNotExist as exc:
        raise ValidationError("User is not a member of this project.") from exc

    with transaction.atomic():
        membership.delete()
        record_audit_event(
            action=AuditAction.PROJECT_UPDATED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=project,
            details={"operation": "removed", "member_id": member.pk},
        )


def create_site(
    *,
    actor: User,
    project: Project,
    name: str,
    coordinates: Point,
    coordinate_reference_system: str = "EPSG:4326",
) -> Site:
    _validate_site_management_actor(actor=actor, project=project)
    validated_coordinates = _validate_site_coordinates(coordinates)

    with transaction.atomic():
        site = Site.objects.create(
            project=project,
            name=name,
            coordinates=validated_coordinates,
            coordinate_reference_system=coordinate_reference_system,
        )
        record_audit_event(
            action=AuditAction.SITE_CREATED,
            entity_type="site",
            entity_id=site.pk,
            user=actor,
            project=site.project,
        )

    return site


def update_site(
    *,
    actor: User,
    site: Site,
    name: str | object = _UNSET,
    coordinates: Point | object = _UNSET,
    coordinate_reference_system: str | object = _UNSET,
) -> Site:
    _validate_site_management_actor(actor=actor, project=site.project)

    update_fields: list[str] = []

    if name is not _UNSET:
        site.name = name
        update_fields.append("name")

    if coordinates is not _UNSET:
        site.coordinates = _validate_site_coordinates(coordinates)
        update_fields.append("coordinates")

    if coordinate_reference_system is not _UNSET:
        site.coordinate_reference_system = coordinate_reference_system
        update_fields.append("coordinate_reference_system")

    if not update_fields:
        return site

    with transaction.atomic():
        site.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=AuditAction.SITE_UPDATED,
            entity_type="site",
            entity_id=site.pk,
            user=actor,
            project=site.project,
        )

    return site


def delete_site(*, actor: User, site: Site) -> None:
    _validate_site_management_actor(actor=actor, project=site.project)

    with transaction.atomic():
        site_id = site.pk
        project = site.project
        site.delete()
        record_audit_event(
            action=AuditAction.SITE_DELETED,
            entity_type="site",
            entity_id=site_id,
            user=actor,
            project=project,
        )


def _validate_project_manager(project_manager: User | None) -> None:
    if project_manager is None:
        raise ValidationError("A project manager assignment is required.")

    if not project_manager.is_active:
        raise ValidationError("Project manager must be active.")

    if project_manager.role != UserRole.PROJECT_MANAGER:
        raise ValidationError("Assigned project manager must have the PROJECT_MANAGER role.")


def _validate_project_membership_actor(*, actor: User, project: Project) -> None:
    if not user_can_manage_project(actor, project):
        raise PermissionDenied(
            "Only active administrators and the owning project manager can manage project membership."
        )

    if project.status != "active":
        raise ValidationError("Only active projects can have membership changes.")


def _validate_project_membership_target(member: User) -> None:
    if not member.is_active:
        raise ValidationError("Project members must be active users.")

    if member.role not in {UserRole.SURVEY_ENGINEER, UserRole.VIEWER}:
        raise ValidationError("Project members must have the SURVEY_ENGINEER or VIEWER role.")


def _validate_site_management_actor(*, actor: User, project: Project) -> None:
    if not user_can_manage_project(actor, project):
        raise PermissionDenied(
            "Only active administrators and the owning project manager can manage sites."
        )

    if project.status != "active":
        raise ValidationError("Only active projects can have site changes.")


def _validate_site_coordinates(coordinates: Point) -> Point:
    if not isinstance(coordinates, Point):
        raise ValidationError("Site coordinates must be a valid Point with SRID 4326.")

    if coordinates.srid != 4326:
        raise ValidationError("Site coordinates must use SRID 4326.")

    return coordinates


def _user_has_project_membership(*, user: User, project: Project) -> bool:
    return ProjectMembership.objects.filter(project=project, user=user).exists()
