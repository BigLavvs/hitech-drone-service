from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError
from rest_framework.throttling import ScopedRateThrottle

from apps.access_control.authentication import HitechJWTAuthentication
from apps.files.serializers import ProcessingJobDetailSerializer
from apps.processing.models import ProcessingJob
from apps.processing.services import get_processing_job_visible_to_user, manual_retry_processing_job


class RetryScopedRateThrottle(ScopedRateThrottle):
    scope = "retry"

    def get_rate(self):
        return settings.RATE_LIMIT_RETRY


class ProcessingJobDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProcessingJobDetailSerializer

    def get(self, request, processing_job_id, *args, **kwargs):
        job = _get_visible_job_or_404(user=request.user, processing_job_id=processing_job_id)
        return Response(ProcessingJobDetailSerializer(job).data)


class ProcessingJobRetryAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "retry"
    serializer_class = ProcessingJobDetailSerializer

    def post(self, request, processing_job_id, *args, **kwargs):
        _get_visible_job_or_404(user=request.user, processing_job_id=processing_job_id)

        try:
            job = manual_retry_processing_job(
                actor=request.user,
                processing_job_id=processing_job_id,
            )
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        job = ProcessingJob.objects.select_related("file", "file__processing_job").get(pk=job.pk)
        return Response(ProcessingJobDetailSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    def get_throttles(self):
        if self.request.method == "POST":
            return [RetryScopedRateThrottle()]
        return []


def _get_visible_job_or_404(*, user, processing_job_id: int):
    try:
        return get_processing_job_visible_to_user(
            actor=user,
            processing_job_id=processing_job_id,
        )
    except ProcessingJob.DoesNotExist as exc:
        raise Http404 from exc


def _to_drf_validation_error(exc: DjangoValidationError) -> DRFValidationError:
    if hasattr(exc, "message_dict"):
        return DRFValidationError(exc.message_dict)
    if hasattr(exc, "messages"):
        return DRFValidationError(exc.messages)
    return DRFValidationError(str(exc))
