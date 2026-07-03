from rest_framework import serializers


def create_response_serializer(data_serializer_class=None, text_message=""):
    class ResponseSerializer(serializers.Serializer):
        message = serializers.CharField(default=text_message)
        data = data_serializer_class() if data_serializer_class else serializers.DictField(default={})

        class Meta:
            ref_name = (
                f"ResponseSerializer_{data_serializer_class.__name__}"
                if data_serializer_class else
                f"ResponseSerializer_{text_message.replace(' ', '_')[:30]}"
            )

    return ResponseSerializer


def create_paginated_response_serializer(data_serializer_class):
    class PaginatedDataSerializer(serializers.Serializer):
        count = serializers.IntegerField()
        total_pages = serializers.IntegerField()
        next = serializers.URLField(allow_null=True)
        previous = serializers.URLField(allow_null=True)
        results = data_serializer_class(many=True)

        class Meta:
            ref_name = f"PaginatedData_{data_serializer_class.__name__}"

    class ResponseSerializer(serializers.Serializer):
        message = serializers.CharField()
        data = PaginatedDataSerializer()

        class Meta:
            ref_name = f"PaginatedResponse_{data_serializer_class.__name__}"

    return ResponseSerializer
