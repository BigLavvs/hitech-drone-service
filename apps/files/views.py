from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpResponseRedirect
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.throttling import ScopedRateThrottle

from apps.access_control.authentication import HitechJWTAuthentication
from apps.files.models import SurveyFile
from apps.files.serializers import (
    SurveyFileListItemSerializer,
    SurveyFileUploadResponseSerializer,
)
from apps.files.services import (
    admit_uploaded_file,
    get_survey_file_download_for_user,
    get_survey_files_visible_to_user,
)
from apps.files.validation import FileValidationError
from apps.surveys.models import Survey


class UploadScopedRateThrottle(ScopedRateThrottle):
    scope = "upload"

    def get_rate(self):
        return settings.RATE_LIMIT_UPLOAD


class SurveyFileListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser]
    throttle_scope = "upload"
    serializer_class = SurveyFileListItemSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SurveyFileUploadResponseSerializer
        return SurveyFileListItemSerializer

    def get(self, request, survey_id, *args, **kwargs):
        try:
            queryset = get_survey_files_visible_to_user(actor=request.user, survey_id=survey_id)
        except Survey.DoesNotExist as exc:
            raise Http404 from exc

        return Response(SurveyFileListItemSerializer(queryset, many=True).data)

    def post(self, request, survey_id, *args, **kwargs):
        if not request.content_type or not request.content_type.lower().startswith("multipart/form-data"):
            return Response(
                {"detail": "Content-Type must be multipart/form-data."},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        unexpected_file_fields = set(request.FILES.keys()) - {"file", "assets"}
        unexpected_data_fields = set(request.data.keys()) - {"file", "assets"}
        if unexpected_file_fields or unexpected_data_fields:
            raise DRFValidationError("Only the 'file' field and repeated 'assets' fields are allowed.")

        _validate_multipart_file_only_field(request=request, field_name="file", require_exactly_one=True)
        _validate_multipart_file_only_field(request=request, field_name="assets", require_exactly_one=False)

        primary_files = request.FILES.getlist("file")
        if len(primary_files) != 1:
            raise DRFValidationError("Exactly one primary 'file' upload is required.")

        asset_files = request.FILES.getlist("assets")

        try:
            admission_result = admit_uploaded_file(
                actor=request.user,
                survey=_get_survey_stub_or_404(survey_id=survey_id),
                uploaded_file=primary_files[0],
                asset_files=asset_files,
            )
        except FileValidationError as exc:
            raise DRFValidationError(str(exc)) from exc
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        response_status = status.HTTP_202_ACCEPTED if admission_result.created else status.HTTP_200_OK
        return Response(
            SurveyFileUploadResponseSerializer(admission_result).data,
            status=response_status,
        )

    def get_throttles(self):
        if self.request.method == "POST":
            return [UploadScopedRateThrottle()]
        return []


def _get_survey_stub_or_404(*, survey_id: int):
    try:
        return Survey.objects.select_related("project").get(pk=survey_id)
    except Survey.DoesNotExist as exc:
        raise Http404 from exc


def _to_drf_validation_error(exc: DjangoValidationError) -> DRFValidationError:
    if hasattr(exc, "message_dict"):
        return DRFValidationError(exc.message_dict)
    if hasattr(exc, "messages"):
        return DRFValidationError(exc.messages)
    return DRFValidationError(str(exc))


def _validate_multipart_file_only_field(*, request, field_name: str, require_exactly_one: bool):
    values = request.data.getlist(field_name)
    if not values:
        return

    if not all(isinstance(value, UploadedFile) for value in values):
        raise DRFValidationError(f"The '{field_name}' field must contain only file uploads.")

        if require_exactly_one and len(values) != 1:
            raise DRFValidationError("Exactly one primary 'file' upload is required.")


class SurveyFileDownloadAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SurveyFileListItemSerializer

    @extend_schema(
        summary="Download the original uploaded file",
        responses={302: OpenApiResponse(description="Short-lived private download redirect.")},
    )
    def get(self, request, survey_id, file_id, *args, **kwargs):
        try:
            result = get_survey_file_download_for_user(
                actor=request.user,
                survey_id=survey_id,
                file_id=file_id,
            )
        except Survey.DoesNotExist as exc:
            raise Http404 from exc
        except SurveyFile.DoesNotExist as exc:
            raise Http404 from exc

        return HttpResponseRedirect(result.download_url)
