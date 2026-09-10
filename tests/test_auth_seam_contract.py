"""frontend/src/auth 接缝的结构契约。

tickerkeep-cloud 在固定 commit 上构建 core，并用自己的 routes.ts / index.tsx /
session.tsx 覆盖 frontend/src/auth/（与 marketing/、enterprise/ 同一套机制）。
这里只做源码文本层面的断言（文件存在、导出名、App.tsx 只经接缝取登录状态、
接缝内不含 cloud 认证词汇）；不做类型检查，那是 `pnpm build` 的事。
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = PROJECT_ROOT / "frontend" / "src"
AUTH_SEAM = FRONTEND_SRC / "auth"
APP_TSX = FRONTEND_SRC / "App.tsx"
ACCOUNT_MENU_TSX = FRONTEND_SRC / "components" / "AccountMenu.tsx"

# The three files cloud's build.sh-style overlay must find at the pin and must
# itself provide; core's directory holds exactly these and nothing else.
SEAM_FILES = ("routes.ts", "index.tsx", "session.tsx")

# Named exports App.tsx / AccountMenu.tsx import; an overlay must keep them.
SEAM_EXPORTS = {
    "routes.ts": (
        r"^export const AUTH_PATHS\b",
        r"^export const LOGIN_PATH\b",
        r"^export function isAuthPath\(pathname: string\): boolean",
    ),
    "index.tsx": (
        r"^export default function AuthRoutes\(\)",
    ),
    "session.tsx": (
        r"^export type AuthStatus = 'checking' \| 'authenticated' \| 'unauthenticated'",
        r"^export function useAuthSession\(\): \{ status: AuthStatus \}",
        r"^export function signOut\(\): void \| Promise<void>",
    ),
}

# Vocabulary of the cloud edition's auth (cookie session, CSRF double-submit,
# email signup/verification). None of it may appear in core's seam or in the
# shell component that calls the seam.
CLOUD_AUTH_VOCABULARY = (
    "csrf",
    "X-CSRF-Token",
    "verify-email",
    "signup",
    "credentials: 'include'",
    'credentials: "include"',
    "__Host-",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def _source_files(root: Path):
    return sorted(p for p in root.rglob("*") if p.suffix in {".ts", ".tsx"})


def test_seam_directory_holds_exactly_the_contract_files():
    """frontend/src/auth 恰好包含 routes.ts / index.tsx / session.tsx 三个文件"""
    assert AUTH_SEAM.is_dir()
    assert sorted(p.name for p in AUTH_SEAM.iterdir() if p.name != ".DS_Store") == sorted(SEAM_FILES)


def test_seam_files_export_the_contract_symbols():
    """接缝三文件各自导出契约规定的符号与签名"""
    for name, patterns in SEAM_EXPORTS.items():
        src = _read(AUTH_SEAM / name)
        for pattern in patterns:
            assert re.search(pattern, src, re.MULTILINE), f"auth/{name} lacks export matching {pattern!r}"


def test_seam_files_document_the_overlay_contract():
    """接缝三文件都有说明 overlay 契约的头注释"""
    for name in SEAM_FILES:
        src = _read(AUTH_SEAM / name)
        assert src.lstrip().startswith("/**"), f"auth/{name} must open with a doc comment"
        head = src[: src.index("*/")]
        assert "Overlay contract" in head, f"auth/{name} doc comment must describe the overlay contract"
        assert "pinned commit" in head, f"auth/{name} doc comment must say cloud replaces it at the pinned commit"


def test_core_seam_is_self_hosted_login():
    """core 的接缝实现是自托管版：/login 一个路径，包了 pages/Login 与 @tickerkeep/api 的 isAuthenticated/logout"""
    routes = _read(AUTH_SEAM / "routes.ts")
    assert re.search(r"^export const AUTH_PATHS: readonly string\[\] = \['/login'\]", routes, re.MULTILINE)
    assert re.search(r"^export const LOGIN_PATH: string = '/login'", routes, re.MULTILINE)

    index = _read(AUTH_SEAM / "index.tsx")
    assert "import('@/pages/Login')" in index
    assert (FRONTEND_SRC / "pages" / "Login.tsx").is_file(), "core's Login page stays at pages/Login.tsx"

    session = _read(AUTH_SEAM / "session.tsx")
    assert "import { isAuthenticated, logout } from '@tickerkeep/api'" in session
    assert "useState<AuthStatus>('checking')" in session


def test_app_tsx_routes_login_through_the_seam():
    """App.tsx 不再硬编码 '/login'，也不直接用 isAuthenticated，改从 @/auth 取路径与会话状态"""
    src = _read(APP_TSX)
    assert "'/login'" not in src and '"/login"' not in src, "App.tsx must not hard-code the login path"
    assert "isAuthenticated" not in src, "App.tsx must read session state via useAuthSession()"
    assert "pages/Login" not in src, "App.tsx must not import the login page directly; the seam does"
    assert "from '@/auth/routes'" in src
    assert "from '@/auth/session'" in src
    assert "import('@/auth')" in src
    assert "isAuthPath(location.pathname)" in src
    assert "<AuthRoutes />" in src
    assert "<Navigate to={LOGIN_PATH}" in src
    assert "useAuthSession()" in src


def test_nothing_outside_the_seam_uses_core_auth_helpers_directly():
    """frontend/src 中除接缝外没有文件直接导入 @tickerkeep/api 的 isAuthenticated / logout"""
    offenders = []
    for path in _source_files(FRONTEND_SRC):
        if AUTH_SEAM in path.parents:
            continue
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^import\s*\{([^}]*)\}\s*from\s*'@tickerkeep/api'", src, re.MULTILINE):
            names = {n.strip().split(" as ")[0] for n in m.group(1).split(",")}
            if names & {"isAuthenticated", "logout"}:
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert offenders == [], f"route these through @/auth/session: {offenders}"


def test_account_menu_signs_out_through_the_seam():
    """AccountMenu 经 useAuthSession/signOut 判断登录并登出，并带两个 e2e 用的 data-testid"""
    src = _read(ACCOUNT_MENU_TSX)
    assert "import { useAuthSession, signOut } from '@/auth/session'" in src
    assert "useAuthSession()" in src
    assert "signOut()" in src
    assert src.count('data-testid="account-menu-trigger"') == 1
    assert src.count('data-testid="auth-signout"') == 1
    assert "data-testid" not in src.replace('data-testid="account-menu-trigger"', "").replace('data-testid="auth-signout"', ""), (
        "AccountMenu carries exactly the two agreed test ids"
    )


def test_core_seam_contains_no_cloud_auth_vocabulary():
    """core 接缝与 AccountMenu 中不含 cloud 认证词汇（csrf / verify-email / signup / cookie 凭据 / __Host-）"""
    files = _source_files(AUTH_SEAM) + [ACCOUNT_MENU_TSX]
    hits = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        for word in CLOUD_AUTH_VOCABULARY:
            if word.lower() in src.lower():
                hits.append(f"{path.relative_to(PROJECT_ROOT)}: {word!r}")
    assert hits == [], "core must contain zero cloud-auth code:\n" + "\n".join(hits)
