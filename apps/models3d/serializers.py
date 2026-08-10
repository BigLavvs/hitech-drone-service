from rest_framework import serializers


class ModelDescriptorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    original_filename = serializers.CharField()
    format = serializers.CharField()
    viewer_source_type = serializers.CharField()
    source_url = serializers.URLField()
    display_format = serializers.CharField(required=False, allow_null=True)
    vertex_count = serializers.IntegerField(required=False, allow_null=True)
    bounding_box = serializers.JSONField(required=False, allow_null=True)
    crs = serializers.CharField(required=False, allow_null=True)

    def to_representation(self, instance):
        payload = super().to_representation(instance)
        return {key: value for key, value in payload.items() if value is not None}
