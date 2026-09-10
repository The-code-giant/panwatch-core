"""`@tickerkeep/api`'s CSRF-token-provider seam: a source-text contract.

This repository has no JavaScript/TypeScript test runner (no vitest, no
jest, nothing under frontend/*/package.json's "scripts"; `pnpm build` only
runs `tsc -b && vite build`), so the established pattern for asserting a
TypeScript contract from pytest is a structural read of the source, the same
approach `test_auth_seam_contract.py` uses for the auth seam. These tests
read frontend/packages/api/src/client.ts (and index.ts) and assert the shape
that gives fetchAPI its default-inert / registered-provider / same-origin-only
behaviour; `tsc -b` (part of `pnpm build`) is what actually type-checks it.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SRC = PROJECT_ROOT / "frontend" / "packages" / "api" / "src"
CLIENT_TS = API_SRC / "client.ts"
INDEX_TS = API_SRC / "index.ts"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_provider_slot_defaults_to_null():
    """模块级 provider 变量默认是 null：未注册时就是核心的默认状态"""
    src = _read(CLIENT_TS)
    assert re.search(r"let\s+csrfTokenProvider\s*:\s*CsrfTokenProvider\s*\|\s*null\s*=\s*null", src), (
        "csrfTokenProvider must default to null (no provider registered)"
    )


def test_setter_is_exported_and_can_clear_the_provider():
    """setCsrfTokenProvider 是具名导出，且接受 null 以清除已注册的 provider"""
    src = _read(CLIENT_TS)
    assert re.search(
        r"export function setCsrfTokenProvider\(provider: CsrfTokenProvider \| null\): void",
        src,
    )
    assert "csrfTokenProvider = provider" in src


def test_index_reexports_the_client_module_wholesale():
    """packages/api 的 index.ts 整体转出 client.ts，setCsrfTokenProvider 因此自动可从 @tickerkeep/api 导入"""
    src = _read(INDEX_TS)
    assert re.search(r"^export \* from '\./client'\s*$", src, re.MULTILINE), (
        "index.ts must re-export client.ts wholesale so the new provider hook reaches @tickerkeep/api consumers"
    )


def test_fetchapi_only_consults_the_provider_for_unsafe_methods():
    """fetchAPI 只在非 GET/HEAD 方法时才询问 provider（安全方法从不带 CSRF 头）"""
    src = _read(CLIENT_TS)
    assert 'const SAFE_METHODS = new Set([\'GET\', \'HEAD\'])' in src
    # The guard around the provider call must check both "a provider is
    # registered" and "the method is not in SAFE_METHODS" before awaiting it.
    guard = re.search(
        r"if \(csrfTokenProvider && !SAFE_METHODS\.has\(method\)\) \{\s*"
        r"const csrfToken = await csrfTokenProvider\(path, method\)\s*"
        r"if \(csrfToken\) \{\s*headers\[CSRF_HEADER\] = csrfToken\s*\}\s*\}",
        src,
    )
    assert guard, "fetchAPI must gate the provider call on (provider registered) AND (method not safe)"


def test_default_inert_no_header_key_hardcoded_unconditionally():
    """核心默认无 provider：X-CSRF-Token 只在守卫内被设置一次，没有第二条无条件写入路径"""
    src = _read(CLIENT_TS)
    # The header name is used exactly twice: once in the CSRF_HEADER constant
    # definition, once inside the guarded assignment above. Any additional
    # occurrence would be a second, possibly-unconditional write path.
    assert src.count("CSRF_HEADER") == 2, (
        "X-CSRF-Token must be attached from exactly one place, the guarded provider branch"
    )
    assert re.search(r"const CSRF_HEADER = 'X-CSRF-Token'", src)


def test_provider_receives_path_and_resolved_uppercase_method():
    """provider 收到的是 fetchAPI 的原始 path 与解析后的大写方法名"""
    src = _read(CLIENT_TS)
    assert "const method = (options?.method || 'GET').toUpperCase()" in src
    assert "csrfTokenProvider(path, method)" in src


def test_requests_are_structurally_same_origin_only():
    """fetchAPI 只会请求根相对的 `${API_BASE}${path}`，不存在构造跨域请求的分支"""
    src = _read(CLIENT_TS)
    assert re.search(r"const API_BASE = '/api'", src), "API_BASE must be root-relative"
    fetch_calls = re.findall(r"fetch\(([^,]+),", src)
    assert fetch_calls == ["`${API_BASE}${path}`"], (
        f"fetchAPI must call fetch() with exactly one, root-relative target; found {fetch_calls}"
    )
