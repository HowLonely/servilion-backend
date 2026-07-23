from ninja import Schema


class PresignedUploadOut(Schema):
    """Respuesta para subir un archivo directo a S3 desde el cliente (web/app).

    El backend nunca recibe el archivo: solo entrega una URL y campos firmados
    que el cliente usa para hacer un POST/PUT multipart directo a S3.
    """

    upload_url: str
    fields: dict[str, str]
    object_key: str


class MessageOut(Schema):
    detail: str
