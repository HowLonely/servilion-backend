import uuid

import boto3
from django.conf import settings


def _s3_client():
    return boto3.client(
        's3',
        region_name=settings.AWS_S3_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
    )


def build_presigned_upload(folder: str, filename: str, content_type: str) -> dict:
    """Genera una URL presignada (POST) para que el cliente suba un archivo
    directo a S3, sin que el archivo pase por el backend.

    `folder` agrupa el objeto dentro del bucket (ej. "empresas/logos",
    "guias/fotos"). El nombre real del objeto se aleatoriza para evitar
    colisiones y no depender del nombre original del archivo.
    """
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'bin'
    object_key = f'{folder}/{uuid.uuid4()}.{extension}'

    response = _s3_client().generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=object_key,
        Fields={'Content-Type': content_type},
        Conditions=[{'Content-Type': content_type}],
        ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRATION,
    )

    return {
        'upload_url': response['url'],
        'fields': response['fields'],
        'object_key': object_key,
    }


def build_object_url(object_key: str | None) -> str | None:
    """Devuelve la URL pública/servible de un objeto ya subido a S3."""
    if not object_key:
        return None
    return f'https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{object_key}'
