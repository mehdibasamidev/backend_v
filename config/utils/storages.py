from django.conf import settings
from storages.backends.s3boto3 import S3Boto3Storage


class PublicMediaStorage(S3Boto3Storage):
    """
    Profile pictures and anything else safe to hand out openly.

    Relies on AWS_S3_CUSTOM_DOMAIN so URLs point at the public MinIO
    hostname rather than the docker-internal one. NOTE: custom_domain
    replaces the whole host+bucket portion, so it must INCLUDE the bucket
    (e.g. "minio.example.com/media") or the key resolves at the domain root.

    Unsigned URLs, so this bucket must have an anonymous download policy.
    """
    default_acl = "public-read"
    querystring_auth = False
    file_overwrite = False


class PrivateMediaStorage(S3Boto3Storage):
    """
    Payment receipts - bank documents, so nothing here may be publicly
    reachable. Files are streamed by apps/vpn/views/receipt.py after a
    permission check.

    custom_domain is forced off: AWS_S3_CUSTOM_DOMAIN is a global setting,
    and inheriting it here would make .url() emit a public link for a
    private object. This bucket must have NO anonymous policy.
    """
    default_acl = None
    querystring_auth = False
    file_overwrite = False

    # Forced off regardless of the global AWS_S3_CUSTOM_DOMAIN setting.
    # S3Boto3Storage assigns these in __init__, so the setters swallow the
    # assignment instead of raising.
    @property
    def custom_domain(self):
        return None

    @custom_domain.setter
    def custom_domain(self, value):
        pass

    @property
    def bucket_name(self):
        return getattr(settings, "AWS_PRIVATE_STORAGE_BUCKET_NAME", "private")

    @bucket_name.setter
    def bucket_name(self, value):
        pass
