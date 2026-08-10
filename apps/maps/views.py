from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404, HttpResponseRedirect
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.serializers import ValidationError as DRFValidationError

from apps.access_control.authentication import HitechJWTAuthentication
from apps.files.models import SurveyFile
from apps.maps.models import Measurement
from apps.maps.serializers import (
    MapLayerDescriptorSerializer,
    MeasurementReadSerializer,
    MeasurementWriteSerializer,
)
from apps.maps.services import (
    create_measurement,
    delete_measurement,
    get_map_tile_redirect_for_user,
    get_measurement_visible_to_user,
    get_measurements_visible_to_user,
    get_survey_map_layers_for_user,
)
from apps.surveys.models import Survey


class SurveyMapLayerListAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MapLayerDescriptorSerializer

    def get(self, request, survey_id, *args, **kwargs):
        try:
            descriptors = get_survey_map_layers_for_user(actor=request.user, survey_id=survey_id)
        except Survey.DoesNotExist as exc:
            raise Http404 from exc

        return Response(MapLayerDescriptorSerializer(descriptors, many=True).data)


class SurveyMeasurementListCreateAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MeasurementReadSerializer

    def get_serializer_class(self):
        if self.request.method == "POST":
            return MeasurementWriteSerializer
        return MeasurementReadSerializer

    def get(self, request, survey_id, *args, **kwargs):
        try:
            measurements = get_measurements_visible_to_user(actor=request.user, survey_id=survey_id)
        except Survey.DoesNotExist as exc:
            raise Http404 from exc

        return Response(MeasurementReadSerializer(measurements, many=True).data)

    def post(self, request, survey_id, *args, **kwargs):
        serializer = MeasurementWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            measurement = create_measurement(
                actor=request.user,
                survey_id=survey_id,
                measurement_type=serializer.validated_data["type"],
                name=serializer.validated_data["name"],
                coordinates=serializer.validated_data["coordinates"],
            )
        except Survey.DoesNotExist as exc:
            raise Http404 from exc
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(MeasurementReadSerializer(measurement).data, status=status.HTTP_201_CREATED)


class SurveyMeasurementDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MeasurementReadSerializer

    def get(self, request, survey_id, measurement_id, *args, **kwargs):
        try:
            measurement = get_measurement_visible_to_user(
                actor=request.user,
                survey_id=survey_id,
                measurement_id=measurement_id,
            )
        except (Survey.DoesNotExist, Measurement.DoesNotExist) as exc:
            raise Http404 from exc

        return Response(MeasurementReadSerializer(measurement).data)

    def delete(self, request, survey_id, measurement_id, *args, **kwargs):
        try:
            delete_measurement(
                actor=request.user,
                survey_id=survey_id,
                measurement_id=measurement_id,
            )
        except (Survey.DoesNotExist, Measurement.DoesNotExist) as exc:
            raise Http404 from exc
        except DjangoValidationError as exc:
            raise _to_drf_validation_error(exc) from exc

        return Response(status=status.HTTP_204_NO_CONTENT)


class MapLayerTileRedirectAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = MapLayerDescriptorSerializer

    @extend_schema(
        summary="Redirect to a private rendered map tile",
        responses={302: OpenApiResponse(description="Short-lived private tile redirect.")},
    )
    def get(self, request, file_id, z, x, y, *args, **kwargs):
        try:
            result = get_map_tile_redirect_for_user(
                actor=request.user,
                file_id=file_id,
                z=z,
                x=x,
                y=y,
            )
        except SurveyFile.DoesNotExist as exc:
            raise Http404 from exc

        return HttpResponseRedirect(result.redirect_url)


def _to_drf_validation_error(exc: DjangoValidationError) -> DRFValidationError:
    if hasattr(exc, "message_dict"):
        return DRFValidationError(exc.message_dict)
    if hasattr(exc, "messages"):
        return DRFValidationError(exc.messages)
    return DRFValidationError(str(exc))
