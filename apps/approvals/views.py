from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError

from apps.access_control.authentication import HitechJWTAuthentication
from apps.approvals.models import Approval
from apps.approvals.serializers import (
    ApprovalRejectSerializer,
    ApprovalSummarySerializer,
    EmptyApprovalActionSerializer,
)
from apps.approvals.services import (
    ApprovalConflictError,
    approve_survey,
    get_approval_summary,
    reject_survey,
    submit_survey_for_approval,
)
from apps.surveys.models import Survey
from apps.surveys.serializers import SurveyReadSerializer
from apps.surveys.services import archive_survey_after_review, get_survey_visible_to_user


class SurveySubmitAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EmptyApprovalActionSerializer

    def post(self, request, survey_id, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)

        try:
            submit_survey_for_approval(actor=request.user, survey=survey)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(status=status.HTTP_200_OK)


class SurveyApproveAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EmptyApprovalActionSerializer

    def post(self, request, survey_id, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)

        try:
            approve_survey(actor=request.user, survey=survey)
        except ApprovalConflictError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(status=status.HTTP_200_OK)


class SurveyRejectAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ApprovalRejectSerializer

    def post(self, request, survey_id, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)

        try:
            reject_survey(
                actor=request.user,
                survey=survey,
                reason=serializer.validated_data["reason"],
            )
        except ApprovalConflictError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(status=status.HTTP_200_OK)


class SurveyArchiveAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SurveyReadSerializer

    def post(self, request, survey_id, *args, **kwargs):
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)

        try:
            archived_survey = archive_survey_after_review(actor=request.user, survey=survey)
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(SurveyReadSerializer(archived_survey).data, status=status.HTTP_200_OK)


class SurveyApprovalDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ApprovalSummarySerializer

    def get(self, request, survey_id, *args, **kwargs):
        survey = _get_visible_survey_or_404(user=request.user, survey_id=survey_id)

        try:
            summary = get_approval_summary(survey=survey)
        except Approval.DoesNotExist as exc:
            raise Http404 from exc

        return Response(
            {
                "survey_id": summary.survey_id,
                "current_status": summary.current_status,
                "submitted_at": summary.submitted_at,
                "submitted_by": summary.submitted_by,
                "approved_at": summary.approved_at,
                "approved_by": summary.approved_by,
                "rejection_reason": summary.rejection_reason,
                "history": summary.history,
            }
        )


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
