"""``GET /auth/me`` 能力契约单测:``user`` 键保持原样，新增 ``capabilities`` 扁平列表。

全自包含:内存 SQLite + TestClient，不连外部、不读安装数据。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.web.api.auth as auth_module
from src.web.database import Base, get_db

# 企业页面依赖的能力名;核心不实现这些功能，只发布名称。
EXPECTED_CAPABILITIES = {
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
}

# 产品房间(frontend/src/components/shell/rooms.ts)依赖的能力名，按契约固定顺序
# 追加在上面十一个之后。核心是单操作员部署，全部无条件授予；云端
# (cloud/capabilities.py)按角色只授予其中一部分，且从不授予 discover:read /
# agents:read / reports:read —— 云端没有对应的发现、Agent 或报告功能管线。
PRODUCT_CAPABILITIES_IN_ORDER = (
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


def _setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(auth_module.router, prefix="/api/auth")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    # JWT secret 走内存库，且不复用其它用例缓存的密钥。
    monkeypatch.setattr(auth_module, "SessionLocal", Session)
    monkeypatch.setattr(auth_module, "_jwt_secret", None)
    return TestClient(app)


def test_operator_capabilities_constant_is_named_tuple():
    """OPERATOR_CAPABILITIES 是模块级 tuple[str, ...]，含企业页面所需的全部能力名"""
    caps = auth_module.OPERATOR_CAPABILITIES
    assert isinstance(caps, tuple)
    assert caps, "capability list must not be empty"
    assert all(isinstance(c, str) for c in caps)
    assert len(set(caps)) == len(caps), "capability names must be unique"
    assert EXPECTED_CAPABILITIES <= set(caps)
    # 命名规范 <noun>:<verb>，verb 只能是 read / manage
    for cap in caps:
        noun, verb = cap.split(":")
        assert noun and verb in {"read", "manage"}, cap


def test_product_capabilities_are_appended_in_the_fixed_order_and_read_only():
    """九个产品能力名紧跟在原有十一个之后，顺序固定，且全部是 :read（没有对应的 :manage）"""
    caps = auth_module.OPERATOR_CAPABILITIES
    assert caps[11:] == PRODUCT_CAPABILITIES_IN_ORDER
    for cap in PRODUCT_CAPABILITIES_IN_ORDER:
        assert cap.endswith(":read"), cap


def test_core_grants_every_product_capability_always():
    """核心单操作员部署：九个产品能力名全部、无条件出现在 OPERATOR_CAPABILITIES 中"""
    assert set(PRODUCT_CAPABILITIES_IN_ORDER) <= set(auth_module.OPERATOR_CAPABILITIES)


def test_me_guest_keeps_user_and_adds_capabilities(monkeypatch):
    """未设置密码时 /auth/me 仍返回 user=guest，并附带完整 capabilities 列表"""
    client = _setup(monkeypatch)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "guest"
    assert isinstance(body["capabilities"], list)
    assert body["capabilities"] == list(auth_module.OPERATOR_CAPABILITIES)
    assert EXPECTED_CAPABILITIES <= set(body["capabilities"])


def test_me_logged_in_keeps_user_and_adds_capabilities(monkeypatch):
    """登录后 /auth/me 返回 user=user(不变)，并附带完整 capabilities 列表"""
    client = _setup(monkeypatch)
    token = client.post(
        "/api/auth/setup", json={"username": "owner", "password": "secret123"}
    ).json()["token"]

    # 设置密码后无 token 必须 401，行为不变
    assert client.get("/api/auth/me").status_code == 401

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "user"
    assert body["capabilities"] == list(auth_module.OPERATOR_CAPABILITIES)
    assert EXPECTED_CAPABILITIES <= set(body["capabilities"])
    assert set(body) == {"user", "capabilities"}
