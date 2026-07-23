from ninja import Router

from authentication.auth import JWTAuth
from authentication.schemas import LoginIn, RefreshIn, TokenOut, UserOut
from authentication.services import AuthError, authenticate_user, issue_tokens, refresh_access_token
from common.schemas import MessageOut

router = Router()


@router.post('/login', response={200: TokenOut, 401: MessageOut}, auth=None)
def login(request, payload: LoginIn):
    try:
        user = authenticate_user(payload.username, payload.password)
    except AuthError as exc:
        return 401, {'detail': str(exc)}
    return 200, issue_tokens(user)


@router.post('/refresh', response={200: TokenOut, 401: MessageOut}, auth=None)
def refresh(request, payload: RefreshIn):
    try:
        return 200, refresh_access_token(payload.refresh)
    except AuthError as exc:
        return 401, {'detail': str(exc)}


@router.get('/me', response=UserOut, auth=JWTAuth())
def me(request):
    return request.auth
