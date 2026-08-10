from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import generics, permissions, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError

from apps.access_control.authentication import HitechJWTAuthentication
from apps.surveys.models import Survey
from apps.surveys.serializers import (
    SurveyCreateSerializer,
    SurveyListQuerySerializer,
    SurveyReadSerializer,
    SurveyUpdateSerializer,
)
from apps.surveys.services import (
    _UNSET,
    create_survey,
    get_survey_visible_to_user,
    get_surveys_visible_to_user,
    update_survey,
)


class SurveyLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class SurveyListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = SurveyLimitOffsetPagination
    serializer_class = SurveyReadSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SurveyCreateSerializer
        return SurveyReadSerializer

    def get(self, request, *args, **kwargs):
        query_serializer = SurveyListQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        queryset = get_surveys_visible_to_user(user=request.user)
        query = query_serializer.validated_data

        if "project_id" in query:
            queryset = queryset.filter(project_id=query["project_id"])

        if "site_id" in query:
            queryset = queryset.filter(site_id=query["site_id"])

        if "status" in query:
            queryset = queryset.filter(status=query["status"])

        if "from_date" in query:
            queryset = queryset.filter(survey_date__gte=query["from_date"])

        if query.get("sort") == "survey_date":
            ordering = ("survey_date", "id") if query["order"] == "asc" else ("-survey_date", "-id")
            queryset = queryset.order_by(*ordering)

        page = self.paginate_queryset(queryset)
        serializer = SurveyReadSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def post(self, request, *args, **kwargs):
        serializer = SurveyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            survey = create_survey(
                actor=request.user,
                project=serializer.validated_data["project"],
                site=serializer.validated_data["site"],
                name=serializer.validated_data["name"],
                survey_date=serializer.validated_data["survey_date"],
                drone_model=serializer.validated_data.get("drone_model", _UNSET),
                pilot=serializer.validated_data.get("pilot", _UNSET),
                coordinate_reference_system=serializer.validated_data.get(
                    "coordinate_reference_system",
                    _UNSET,
                ),
                notes=serializer.validated_data.get("notes", _UNSET),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(SurveyReadSerializer(survey).data, status=status.HTTP_201_CREATED)


class SurveyDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SurveyReadSerializer

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return SurveyUpdateSerializer
        return SurveyReadSerializer

    def get(self, request, survey_id, *args, **kwargs):
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)
        return Response(SurveyReadSerializer(survey).data)

    def patch(self, request, survey_id, *args, **kwargs):
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)
        serializer = SurveyUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            survey = update_survey(
                actor=request.user,
                survey=survey,
                name=serializer.validated_data.get("name", _UNSET),
                survey_date=serializer.validated_data.get("survey_date", _UNSET),
                drone_model=serializer.validated_data.get("drone_model", _UNSET),
                pilot=serializer.validated_data.get("pilot", _UNSET),
                coordinate_reference_system=serializer.validated_data.get(
                    "coordinate_reference_system",
                    _UNSET,
                ),
                notes=serializer.validated_data.get("notes", _UNSET),
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(SurveyReadSerializer(survey).data)


def _get_visible_survey_or_404(*, user, survey_id: int):
    try:
        return get_survey_visible_to_user(user=user, survey_id=survey_id)
    except Survey.DoesNotExist as exc:
        raise Http404 from exc


def _to_drf_validation_error(exc: DjangoValidationError) -> DRFValidationError:
    if hasattr(exc, "message_dict"):
        return DRFValidationError(exc.message_dict)
    if hasattr(exc, "messages"):
        return DRFValidationError(exc.messages)
    return DRFValidationError(str(exc))
