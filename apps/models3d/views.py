from django.http import Http404
from rest_framework import generics, permissions
from rest_framework.response import Response

from apps.access_control.authentication import HitechJWTAuthentication
from apps.models3d.serializers import ModelDescriptorSerializer
from apps.models3d.services import get_survey_models_for_user
from apps.surveys.models import Survey


class SurveyModelListAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ModelDescriptorSerializer

    def get(self, request, survey_id, *args, **kwargs):
        try:
            descriptors = get_survey_models_for_user(actor=request.user, survey_id=survey_id)
        except Survey.DoesNotExist as exc:
            raise Http404 from exc

        return Response(ModelDescriptorSerializer(descriptors, many=True).data)
