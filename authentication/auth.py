from ninja.security import HttpBearer

from authentication.models import User
from authentication.services import AuthError, get_user_from_token
from common.node import is_edge


class JWTAuth(HttpBearer):
    """Autenticación stateless vía cabecera `Authorization: Bearer <token>`.

    Además del JWT de una persona acepta el token de un servidor local
    (`srvnode_...`, ver `sync.Node`): con él la planta consulta en la nube el
    histórico que ya no guarda. Entra como la cuenta técnica del nodo. Solo en
    la nube: el servidor local no atiende a otros nodos (ni trae `sync.cloud`).
    """

    def authenticate(self, request, token: str) -> User | None:
        if not is_edge():
            from sync.cloud import is_node_token, node_from_token

            if is_node_token(token):
                node = node_from_token(token)
                if node is None:
                    return None
                request.user = node.service_user
                request.node = node
                return node.service_user

        try:
            user = get_user_from_token(token, expected_type='access')
        except AuthError:
            return None
        request.user = user
        return user
