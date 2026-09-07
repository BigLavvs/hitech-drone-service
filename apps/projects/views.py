from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError

from apps.access_control.authentication import HitechJWTAuthentication
from apps.access_control.models import UserRole
from apps.projects.models import Project, Site
from apps.projects.serializers import (
    ProjectMemberCandidateSerializer,
    ProjectMemberCreateSerializer,
    ProjectMemberReadSerializer,
    ProjectReadSerializer,
    ProjectWriteSerializer,
    SiteReadSerializer,
    SiteWriteSerializer,
)
from apps.projects.services import (
    _UNSET,
    archive_project,
    create_project,
    create_site,
    delete_site,
    get_available_project_members,
    get_project_member_by_user_id,
    get_project_members,
    get_project_manageable_by_user,
    get_projects_visible_to_user,
    get_project_visible_to_user,
    get_sites_for_project,
    get_site_with_project,
    remove_project_member,
    add_project_member,
    update_project,
    update_site,
)
from config.schema import paginated_response_serializer


class ProjectSiteLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class ProjectListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ProjectSiteLimitOffsetPagination
    serializer_class = ProjectReadSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ProjectWriteSerializer
        return ProjectReadSerializer

    @extend_schema(operation_id="projects_list", responses={200: paginated_response_serializer("PaginatedProjectRead", ProjectReadSerializer)})
    def get(self, request, *args, **kwargs):
        queryset = get_projects_visible_to_user(user=request.user)
        page = self.paginate_queryset(queryset)
        serializer = ProjectReadSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(operation_id="projects_create", request=ProjectWriteSerializer, responses={201: ProjectReadSerializer})
    def post(self, request, *args, **kwargs):
        serializer = ProjectWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if (
            request.user.role == UserRole.PROJECT_MANAGER
            and "project_manager" in serializer.validated_data
        ):
            raise PermissionDenied(
                "Project Managers may not set project_manager_id through the API."
            )

        try:
            project = create_project(
                actor=request.user,
                name=serializer.validated_data["name"],
                description=serializer.validated_data.get("description"),
                location=serializer.validated_data.get("location"),
                project_manager=serializer.validated_data.get("project_manager"),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(ProjectReadSerializer(project).data, status=status.HTTP_201_CREATED)


class ProjectDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectReadSerializer

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return ProjectWriteSerializer
        return ProjectReadSerializer

    @extend_schema(operation_id="project_retrieve", responses={200: ProjectReadSerializer})
    def get(self, request, project_id, *args, **kwargs):
        project = _get_visible_project_or_404(user=request.user, project_id=project_id)
        return Response(ProjectReadSerializer(project).data)

    @extend_schema(operation_id="project_update", request=ProjectWriteSerializer, responses={200: ProjectReadSerializer})
    def patch(self, request, project_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)
        serializer = ProjectWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        if (
            request.user.role == UserRole.PROJECT_MANAGER
            and "project_manager" in serializer.validated_data
        ):
            raise PermissionDenied(
                "Project Managers may not change project_manager_id through the API."
            )

        try:
            project = update_project(
                actor=request.user,
                project=project,
                name=serializer.validated_data.get("name", _UNSET),
                description=serializer.validated_data.get("description", _UNSET),
                location=serializer.validated_data.get("location", _UNSET),
                project_manager=serializer.validated_data.get("project_manager", _UNSET),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(ProjectReadSerializer(project).data)

    @extend_schema(operation_id="project_archive", responses={200: ProjectReadSerializer})
    def delete(self, request, project_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)

        try:
            project = archive_project(actor=request.user, project=project)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(ProjectReadSerializer(project).data)


class ProjectMemberListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectMemberReadSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ProjectMemberCreateSerializer
        return ProjectMemberReadSerializer

    @extend_schema(operation_id="project_members_list", responses={200: ProjectMemberReadSerializer(many=True)})
    def get(self, request, project_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)
        try:
            memberships = get_project_members(actor=request.user, project=project)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc
        return Response(ProjectMemberReadSerializer(memberships, many=True).data)

    @extend_schema(operation_id="project_members_add", request=ProjectMemberCreateSerializer, responses={201: ProjectMemberReadSerializer})
    def post(self, request, project_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)
        serializer = ProjectMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            membership = add_project_member(
                actor=request.user,
                project=project,
                member=serializer.validated_data["user"],
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        member_snapshot = next(
            member for member in get_project_members(actor=request.user, project=project)
            if member.membership_id == membership.pk
        )
        return Response(
            ProjectMemberReadSerializer(member_snapshot).data,
            status=status.HTTP_201_CREATED,
        )


class ProjectAvailableMemberListAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectMemberCandidateSerializer

    @extend_schema(operation_id="project_available_members_list", responses={200: ProjectMemberCandidateSerializer(many=True)})
    def get(self, request, project_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)
        try:
            candidates = get_available_project_members(actor=request.user, project=project)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc
        return Response(ProjectMemberCandidateSerializer(candidates, many=True).data)


class ProjectMemberDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectMemberReadSerializer

    @extend_schema(operation_id="project_member_remove", responses={204: None})
    def delete(self, request, project_id, user_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)

        try:
            member = _get_user_or_404(user_id=user_id)
            remove_project_member(actor=request.user, project=project, member=member)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(status=status.HTTP_204_NO_CONTENT)


class SiteListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = ProjectSiteLimitOffsetPagination
    serializer_class = SiteReadSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SiteWriteSerializer
        return SiteReadSerializer

    @extend_schema(operation_id="project_sites_list", responses={200: paginated_response_serializer("PaginatedSiteRead", SiteReadSerializer)})
    def get(self, request, project_id, *args, **kwargs):
        project = _get_visible_project_or_404(user=request.user, project_id=project_id)
        queryset = get_sites_for_project(project_id=project.pk)
        page = self.paginate_queryset(queryset)
        serializer = SiteReadSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(operation_id="project_sites_create", request=SiteWriteSerializer, responses={201: SiteReadSerializer})
    def post(self, request, project_id, *args, **kwargs):
        project = _get_manageable_project_or_404(user=request.user, project_id=project_id)
        serializer = SiteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            site = create_site(
                actor=request.user,
                project=project,
                name=serializer.validated_data["name"],
                coordinates=serializer.validated_data["coordinates"],
                coordinate_reference_system=serializer.validated_data.get(
                    "coordinate_reference_system",
                    "EPSG:4326",
                ),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(SiteReadSerializer(site).data, status=status.HTTP_201_CREATED)


class SiteDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SiteReadSerializer

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return SiteWriteSerializer
        return SiteReadSerializer

    @extend_schema(operation_id="project_site_retrieve", responses={200: SiteReadSerializer})
    def get(self, request, project_id, site_id, *args, **kwargs):
        site = _get_visible_site_or_404(user=request.user, project_id=project_id, site_id=site_id)
        return Response(SiteReadSerializer(site).data)

    @extend_schema(operation_id="project_site_update", request=SiteWriteSerializer, responses={200: SiteReadSerializer})
    def patch(self, request, project_id, site_id, *args, **kwargs):
        site = _get_manageable_site_or_404(user=request.user, project_id=project_id, site_id=site_id)
        serializer = SiteWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            site = update_site(
                actor=request.user,
                site=site,
                name=serializer.validated_data.get("name", _UNSET),
                coordinates=serializer.validated_data.get("coordinates", _UNSET),
                coordinate_reference_system=serializer.validated_data.get(
                    "coordinate_reference_system",
                    _UNSET,
                ),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(SiteReadSerializer(site).data)

    @extend_schema(operation_id="project_site_delete", responses={204: None})
    def delete(self, request, project_id, site_id, *args, **kwargs):
        site = _get_manageable_site_or_404(user=request.user, project_id=project_id, site_id=site_id)

        try:
            delete_site(actor=request.user, site=site)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(status=status.HTTP_204_NO_CONTENT)


def _get_visible_project_or_404(*, user, project_id: int) -> Project:
    try:
        return get_project_visible_to_user(user=user, project_id=project_id)
    except Project.DoesNotExist as exc:
        raise Http404 from exc


def _get_manageable_project_or_404(*, user, project_id: int) -> Project:
    try:
        return get_project_manageable_by_user(user=user, project_id=project_id)
    except Project.DoesNotExist as exc:
        raise Http404 from exc


def _get_visible_site_or_404(*, user, project_id: int, site_id: int) -> Site:
    project = _get_visible_project_or_404(user=user, project_id=project_id)
    site = _get_site_or_404(site_id=site_id)
    if site.project_id != project.id:
        raise PermissionDenied("You do not have permission to access this site.")
    return site


def _get_manageable_site_or_404(*, user, project_id: int, site_id: int) -> Site:
    project = _get_manageable_project_or_404(user=user, project_id=project_id)
    site = _get_site_or_404(site_id=site_id)
    if site.project_id != project.id:
        raise PermissionDenied("You do not have permission to modify this site.")
    return site


def _get_site_or_404(*, site_id: int) -> Site:
    try:
        return get_site_with_project(site_id=site_id)
    except Site.DoesNotExist as exc:
        raise Http404 from exc


def _get_user_or_404(*, user_id: int):
    try:
        return get_project_member_by_user_id(user_id=user_id)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc


def _to_drf_validation_error(exc: DjangoValidationError) -> DRFValidationError:
    if hasattr(exc, "message_dict"):
        return DRFValidationError(exc.message_dict)
    if hasattr(exc, "messages"):
        return DRFValidationError(exc.messages)
    return DRFValidationError(str(exc))
