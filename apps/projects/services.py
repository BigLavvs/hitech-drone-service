from dataclasses import dataclass

from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.models import Project, ProjectMembership, Site

_UNSET = object()


@dataclass(frozen=True)
class ProjectAccessSnapshot:
    id: int
    status: str
    project_manager_id: int | None


@dataclass(frozen=True)
class ProjectMemberSnapshot:
    membership_id: int
    user_id: int
    email: str
    role: str

    @property
    def id(self):
        return self.user_id


def get_projects_visible_to_user(*, user: User):
    queryset = Project.objects.order_by("id")

    if not user.is_active:
        return queryset.none()

    if user.role == UserRole.ADMINISTRATOR:
        return queryset

    if user.role == UserRole.PROJECT_MANAGER:
        return queryset.filter(project_manager=user)

    if user.role in {UserRole.SURVEY_ENGINEER, UserRole.VIEWER}:
        return queryset.filter(memberships__user_id=user.pk).distinct()

    return queryset.none()


def get_project_ids_visible_to_user(*, user: User):
    return list(get_projects_visible_to_user(user=user).values_list("id", flat=True))


def get_project_access_snapshot(*, project_id: int) -> ProjectAccessSnapshot:
    try:
        project = Project.objects.only("id", "status", "project_manager_id").get(pk=project_id)
    except Project.DoesNotExist:
        raise
    return ProjectAccessSnapshot(
        id=project.pk,
        status=project.status,
        project_manager_id=project.project_manager_id,
    )


def user_can_view_project_id(*, user: User, project_id: int) -> bool:
    try:
        project = Project.objects.only("id", "status", "project_manager_id").get(pk=project_id)
    except Project.DoesNotExist:
        return False
    return user_can_view_project(user, project)


def user_can_manage_project_id(*, user: User, project_id: int) -> bool:
    try:
        project = Project.objects.only("id", "project_manager_id").get(pk=project_id)
    except Project.DoesNotExist:
        return False
    return user_can_manage_project(user, project)


def get_project_visible_to_user(*, user: User, project_id: int) -> Project:
    project = (
        Project.objects
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
        Project.objects
        .filter(pk=project_id)
        .first()
    )
    if project is None:
        raise Project.DoesNotExist

    if not user_can_manage_project(user, project):
        raise PermissionDenied("You do not have permission to modify this project.")

    return project


def get_project_by_id(*, project_id: int) -> Project:
    return Project.objects.get(pk=project_id)


def lock_project_for_update(*, project_id: int, fields=None) -> Project:
    """Lock a project for a transaction coordinated by the projects owner."""
    queryset = Project.objects.select_for_update()
    if fields:
        queryset = queryset.only(*fields)
    return queryset.get(pk=project_id)


def lock_site_for_update(*, site_id: int) -> Site:
    """Lock a site for a transaction coordinated by the projects owner."""
    return Site.objects.select_for_update().get(pk=site_id)


def get_site_with_project(*, site_id: int) -> Site:
    site = Site.objects.select_related("project").filter(pk=site_id).first()
    if site is None:
        raise Site.DoesNotExist
    return site


def get_site_for_project(*, project: Project, site_id: int) -> Site:
    site = Site.objects.select_related("project").filter(pk=site_id, project=project).first()
    if site is None:
        raise Site.DoesNotExist
    return site


def get_project_for_creator_and_name(*, creator: User, name: str) -> Project | None:
    return (
        Project.objects
        .filter(name=name, created_by=creator)
        .first()
    )


def get_site_for_project_and_name(*, project: Project, name: str) -> Site | None:
    return Site.objects.filter(project=project, name=name).first()


def get_sites_for_project(*, project_id: int):
    return Site.objects.filter(project_id=project_id).order_by("id")


def ensure_project_membership(*, project: Project, user: User, assigned_by: User) -> ProjectMembership:
    membership, _created = ProjectMembership.objects.get_or_create(
        project=project,
        user=user,
        defaults={"assigned_by": assigned_by},
    )
    return membership


def user_owns_any_project(*, user: User) -> bool:
    return Project.objects.filter(project_manager=user).exists()


def get_project_member_by_user_id(*, user_id: int) -> User:
    from apps.access_control.services import get_local_user_by_id

    return get_local_user_by_id(user_id=user_id)


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
    update_fields: list[str] = []

    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=project.pk)
        if not user_can_manage_project(actor, locked_project):
            raise PermissionDenied(
                "Only active administrators and the owning project manager can update a project."
            )

        if name is not _UNSET:
            locked_project.name = name
            update_fields.append("name")

        if description is not _UNSET:
            locked_project.description = description
            update_fields.append("description")

        if location is not _UNSET:
            locked_project.location = location
            update_fields.append("location")

        if project_manager is not _UNSET:
            try:
                from apps.access_control.services import lock_user_for_update

                locked_manager = lock_user_for_update(user_id=project_manager.pk)
            except User.DoesNotExist as exc:
                raise ValidationError("Assigned project manager does not exist.") from exc
            _validate_project_manager(locked_manager)
            locked_project.project_manager = locked_manager
            update_fields.append("project_manager")

        if not update_fields:
            return locked_project

        locked_project.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=AuditAction.PROJECT_UPDATED,
            entity_type="project",
            entity_id=locked_project.pk,
            user=actor,
            project=locked_project,
        )

    return locked_project


def synchronize_demo_project(*, actor: User, project: Project, project_manager: User) -> Project:
    """Apply the fixed assessment seed values through the projects owner service."""
    if not user_can_manage_project(actor, project):
        raise PermissionDenied("Only active administrators and the owning project manager can update a project.")
    project.project_manager = project_manager
    project.status = "active"
    project.description = "Development-only assessment project for role-based demo access."
    project.location = "Lagos, Nigeria"
    project.save(update_fields=["project_manager", "status", "description", "location", "updated_at"])
    return project


def archive_project(*, actor: User, project: Project) -> Project:
    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=project.pk)
        if not user_can_manage_project(actor, locked_project):
            raise PermissionDenied(
                "Only active administrators and the owning project manager can archive a project."
            )
        if locked_project.status != "active":
            raise ValidationError("Only active projects can be archived.")

        locked_project.status = "archived"
        locked_project.save(update_fields=["status", "updated_at"])
        record_audit_event(
            action=AuditAction.PROJECT_ARCHIVED,
            entity_type="project",
            entity_id=locked_project.pk,
            user=actor,
            project=locked_project,
        )

    return locked_project


def add_project_member(*, actor: User, project: Project, member: User) -> ProjectMembership:
    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=project.pk)
        _validate_project_membership_actor(actor=actor, project=locked_project)
        from apps.access_control.services import lock_user_for_update

        locked_member = lock_user_for_update(user_id=member.pk)
        _validate_project_membership_target(locked_member)

        if ProjectMembership.objects.filter(project=locked_project, user=locked_member).exists():
            raise ValidationError("User is already a member of this project.")

        membership = ProjectMembership.objects.create(
            project=locked_project,
            user=locked_member,
            assigned_by=actor,
        )
        record_audit_event(
            action=AuditAction.PROJECT_UPDATED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=locked_project,
            details={"operation": "added", "member_id": locked_member.pk},
        )

    return membership


def remove_project_member(*, actor: User, project: Project, member: User) -> None:
    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=project.pk)
        _validate_project_membership_actor(actor=actor, project=locked_project)

        try:
            membership = ProjectMembership.objects.select_for_update().get(
                project=locked_project, user_id=member.pk
            )
        except ProjectMembership.DoesNotExist as exc:
            raise ValidationError("User is not a member of this project.") from exc

        membership.delete()
        record_audit_event(
            action=AuditAction.PROJECT_UPDATED,
            entity_type="project",
            entity_id=project.pk,
            user=actor,
            project=locked_project,
            details={"operation": "removed", "member_id": member.pk},
        )


def get_project_members(*, actor: User, project: Project):
    _validate_project_membership_actor(actor=actor, project=project)
    from apps.access_control.services import get_local_user_snapshots_by_ids

    membership_rows = list(
        ProjectMembership.objects.filter(project_id=project.pk)
        .order_by("id")
        .values("id", "user_id")
    )
    user_snapshots = get_local_user_snapshots_by_ids(
        user_ids=[row["user_id"] for row in membership_rows]
    )
    return tuple(
        ProjectMemberSnapshot(
            membership_id=row["id"],
            user_id=row["user_id"],
            email=user_snapshots[row["user_id"]].email,
            role=user_snapshots[row["user_id"]].role,
        )
        for row in sorted(
            membership_rows,
            key=lambda row: (user_snapshots[row["user_id"]].email, row["id"]),
        )
    )


def get_project_member_user_ids(*, project_id: int):
    return list(
        ProjectMembership.objects.filter(project_id=project_id).values_list(
            "user_id", flat=True
        )
    )


def get_available_project_members(*, actor: User, project: Project):
    _validate_project_membership_actor(actor=actor, project=project)
    from apps.access_control.services import get_active_assignable_project_users

    assigned_user_ids = get_project_member_user_ids(project_id=project.pk)
    return get_active_assignable_project_users(exclude_user_ids=assigned_user_ids)


def create_site(
    *,
    actor: User,
    project: Project,
    name: str,
    coordinates: Point,
    coordinate_reference_system: str = "EPSG:4326",
) -> Site:
    validated_coordinates = _validate_site_coordinates(coordinates)

    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=project.pk)
        _validate_site_management_actor(actor=actor, project=locked_project)
        site = Site.objects.create(
            project=locked_project,
            name=name,
            coordinates=validated_coordinates,
            coordinate_reference_system=coordinate_reference_system,
        )
        record_audit_event(
            action=AuditAction.SITE_CREATED,
            entity_type="site",
            entity_id=site.pk,
            user=actor,
            project=locked_project,
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
    update_fields: list[str] = []
    validated_coordinates = (
        _validate_site_coordinates(coordinates)
        if coordinates is not _UNSET else _UNSET
    )

    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=site.project_id)
        _validate_site_management_actor(actor=actor, project=locked_project)
        locked_site = lock_site_for_update(site_id=site.pk)
        if locked_site.project_id != locked_project.pk:
            raise PermissionDenied("You do not have permission to modify this site.")

        if name is not _UNSET:
            locked_site.name = name
            update_fields.append("name")
        if validated_coordinates is not _UNSET:
            locked_site.coordinates = validated_coordinates
            update_fields.append("coordinates")
        if coordinate_reference_system is not _UNSET:
            locked_site.coordinate_reference_system = coordinate_reference_system
            update_fields.append("coordinate_reference_system")
        if not update_fields:
            return locked_site

        locked_site.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=AuditAction.SITE_UPDATED,
            entity_type="site",
            entity_id=locked_site.pk,
            user=actor,
            project=locked_project,
        )

    return locked_site


def delete_site(*, actor: User, site: Site) -> None:
    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=site.project_id)
        _validate_site_management_actor(actor=actor, project=locked_project)
        locked_site = lock_site_for_update(site_id=site.pk)
        if locked_site.project_id != locked_project.pk:
            raise PermissionDenied("You do not have permission to modify this site.")
        site_id = locked_site.pk
        locked_site.delete()
        record_audit_event(
            action=AuditAction.SITE_DELETED,
            entity_type="site",
            entity_id=site_id,
            user=actor,
            project=locked_project,
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
