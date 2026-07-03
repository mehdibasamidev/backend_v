from drf_yasg.inspectors import FieldInspector
from drf_yasg import openapi
from parler_rest.fields import TranslatedFieldsField


class GenericTranslatedFieldsInspector(FieldInspector):
    def process_result(self, result, method_name, obj, **kwargs):
        if isinstance(obj, TranslatedFieldsField):
            print("[Swagger Inspector] Detected TranslatedFieldsField – returning dummy schema")
            return openapi.Schema(
                type=openapi.TYPE_OBJECT,
                additional_properties=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "placeholder": openapi.Schema(type=openapi.TYPE_STRING)
                    }
                ),
                example={
                    "en": {
                        "placeholder": "example"
                    }
                }
            )
        return result
