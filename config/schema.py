from drf_spectacular.utils import inline_serializer
from rest_framework import serializers


def paginated_response_serializer(name, child_serializer):
    return inline_serializer(
        name=name,
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": child_serializer(many=True),
        },
    )
