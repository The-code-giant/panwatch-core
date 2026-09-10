"""认证 API - 简单的单用户 JWT 认证"""
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt

from src.web.database import get_db, SessionLocal
from src.web.models import AppSettings

router = APIRouter()
security = HTTPBearer(auto_error=False)

# JWT 配置
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# 环境变量配置（Docker 部署用）
ENV_AUTH_USERNAME = os.getenv("AUTH_USERNAME")
ENV_AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# 设置项 key
AUTH_USERNAME_KEY = "auth_username"
PASSWORD_HASH_KEY = "auth_password_hash"
JWT_SECRET_KEY = "jwt_secret"

# JWT Secret 缓存
_jwt_secret: str | None = None

# Capability contract
#
# ``GET /auth/me`` returns a flat list of capability strings next to ``user``.
# Naming scheme is ``<noun>:<verb>``: the noun is the resource family and the
# verb is one of ``read`` (view) or ``manage`` (create / change / delete).
# There is no hierarchy in the strings themselves; a client checks for the exact
# name it needs, and a server that grants ``<noun>:manage`` is expected to also
# grant ``<noun>:read``.
#
# tickerkeep-core is single-operator: ``create_token()`` takes no subject and the
# one signed-in identity owns the whole installation, so core always returns the
# complete list. A multi-tenant deployment fills the same key from the caller's
# organization role and plan entitlements instead. Only the names live here;
# core ships no organization, member, SSO, audit, seat or billing implementation,
# and the strings are the interface that lets a private overlay add those pages
# without inventing its own vocabulary.
#
# The first eleven strings are entirely about admin surfaces (organization,
# membership, SSO, audit, seats, billing) and describe no product room of the
# app itself. The nine appended after them name the product rooms the shell's
# nav (frontend/src/components/shell/rooms.ts) actually gates:
# watchlist/portfolio/paper/alerts (the Portfolio room and its tabs),
# discover, agents/reports (the Agents room and its Reports tab), settings and
# datasources (the Settings room and its Data Sources tab). All nine are
# read-only by design -- there is no product-room ``:manage`` string -- so the
# "manage implies read" invariant above is trivially satisfied by them.
#
# core is still single-operator, so it grants all nine unconditionally, the
# same "always-true" reasoning as the original eleven: there is only ever one
# operator here and they hold everything. A multi-tenant deployment (see
# cloud/capabilities.py) grants a role-dependent SUBSET of these nine instead
# -- it has no discovery, agent or report pipeline, so it never grants
# ``discover:read``, ``agents:read`` or ``reports:read`` regardless of role.
OPERATOR_CAPABILITIES: tuple[str, ...] = (
    "org:read",
    "org:manage",
    "members:read",
    "members:manage",
    "sso:read",
    "sso:manage",
    "audit:read",
    "seats:read",
    "seats:manage",
    "billing:read",
    "billing:manage",
    "watchlist:read",
    "portfolio:read",
    "paper:read",
    "alerts:read",
    "discover:read",
    "agents:read",
    "reports:read",
    "settings:read",
    "datasources:read",
)


def get_jwt_secret() -> str:
    """获取 JWT Secret（持久化到数据库）"""
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret

    # 环境变量优先
    if os.getenv("JWT_SECRET"):
        _jwt_secret = os.getenv("JWT_SECRET")
        return _jwt_secret

    # 从数据库读取或首次生成
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == JWT_SECRET_KEY).first()
        if setting:
            _jwt_secret = setting.value
        else:
            _jwt_secret = secrets.token_hex(32)
            db.add(AppSettings(key=JWT_SECRET_KEY, value=_jwt_secret, description="JWT签名密钥(自动生成)"))
            db.commit()
        return _jwt_secret
    finally:
        db.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    expires_at: str


def hash_password(password: str) -> str:
    """简单的密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()


def create_token(expires_days: int = JWT_EXPIRE_DAYS) -> tuple[str, datetime]:
    """创建 JWT token"""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expires_days)
    payload = {
        "exp": expires_at,
        "iat": now,
        "sub": "user",
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return token, expires_at


def verify_token(token: str) -> bool:
    """验证 JWT token"""
    try:
        jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


def get_stored_username(db: Session) -> Optional[str]:
    """获取存储的用户名"""
    setting = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    return setting.value if setting else None


def set_stored_username(db: Session, username: str):
    """设置用户名"""
    setting = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    if setting:
        setting.value = username
    else:
        setting = AppSettings(key=AUTH_USERNAME_KEY, value=username, description="认证用户名")
        db.add(setting)
    db.commit()


def get_password_hash(db: Session) -> Optional[str]:
    """获取存储的密码哈希"""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    return setting.value if setting else None


def set_password_hash(db: Session, password_hash: str):
    """设置密码哈希"""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    if setting:
        setting.value = password_hash
    else:
        setting = AppSettings(key=PASSWORD_HASH_KEY, value=password_hash, description="认证密码哈希")
        db.add(setting)
    db.commit()


def init_auth_from_env(db: Session) -> bool:
    """从环境变量初始化认证（Docker 部署用）

    Returns:
        True if initialized from env, False otherwise
    """
    if not ENV_AUTH_USERNAME or not ENV_AUTH_PASSWORD:
        return False

    # 如果已有账号，不覆盖
    if get_password_hash(db):
        return False

    # 从环境变量创建账号
    set_stored_username(db, ENV_AUTH_USERNAME)
    set_password_hash(db, hash_password(ENV_AUTH_PASSWORD))
    return True


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    """验证当前用户（用作依赖）"""
    # 检查是否已设置密码
    password_hash = get_password_hash(db)
    if not password_hash:
        # 未设置密码，允许访问（初始状态）
        return None

    # 已设置密码，需要验证 token
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "user"


@router.get("/status")
async def auth_status(db: Session = Depends(get_db)):
    """获取认证状态"""
    password_hash = get_password_hash(db)
    return {
        "initialized": password_hash is not None,
    }


@router.post("/setup", response_model=TokenResponse)
async def setup_password(data: SetupRequest, db: Session = Depends(get_db)):
    """首次设置用户名和密码"""
    if get_password_hash(db):
        raise HTTPException(400, "Account already set up. Please use the login endpoint.")

    if not data.username or len(data.username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")

    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    set_stored_username(db, data.username)
    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """登录"""
    stored_hash = get_password_hash(db)
    stored_username = get_stored_username(db)
    if not stored_hash or not stored_username:
        raise HTTPException(400, "Please set up an account first")

    if data.username != stored_username:
        raise HTTPException(401, "Incorrect username or password")

    if hash_password(data.password) != stored_hash:
        raise HTTPException(401, "Incorrect username or password")

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/change-password")
async def change_password(
    data: SetupRequest,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
):
    """修改密码"""
    if len(data.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    return {"message": "Password updated"}


@router.get("/me")
async def get_me(user: str = Depends(get_current_user)):
    """获取当前用户信息

    ``user`` is unchanged from earlier releases. ``capabilities`` is additive: the
    flat list described next to ``OPERATOR_CAPABILITIES``. Core is single-operator,
    so whoever reaches this endpoint (the signed-in user, or ``guest`` before a
    password has been set) holds every capability.
    """
    return {"user": user or "guest", "capabilities": list(OPERATOR_CAPABILITIES)}
