from django.http import Http404
from rest_framework import generics, permissions
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response

from apps.access_control.authentication import HitechJWTAuthentication
from apps.audit.models import AuditLog
from apps.audit.serializers import AuditLogListQuerySerializer, AuditLogReadSerializer
from apps.audit.services import get_audit_log_visible_to_user, get_audit_logs_visible_to_user


class AuditLogLimitOffsetPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit = 100


class AuditLogListAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = AuditLogLimitOffsetPagination
    serializer_class = AuditLogReadSerializer

    def get(self, request, *args, **kwargs):
        query_serializer = AuditLogListQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        query = query_serializer.validated_data
        queryset = get_audit_logs_visible_to_user(user=request.user, filters=query)

        page = self.paginate_queryset(queryset)
        serializer = AuditLogReadSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class AuditLogDetailAPIView(generics.GenericAPIView):
    authentication_classes = [HitechJWTAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AuditLogReadSerializer

    def get(self, request, audit_log_id, *args, **kwargs):
        try:
            audit_log = get_audit_log_visible_to_user(user=request.user, audit_log_id=audit_log_id)
        except AuditLog.DoesNotExist as exc:
            raise Http404 from exc

        return Response(AuditLogReadSerializer(audit_log).data)
