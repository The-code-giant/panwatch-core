"""The capability-driven nav: rooms.ts, the shell components, and App.tsx's routes.

Structural/source-text contract, in the same spirit as `test_auth_seam_contract.py`
and `test_csrf_provider_seam.py` -- this repository has no JS/TS test runner, so
`tsc -b` (part of `pnpm build`) is what actually type-checks these files; these
tests assert the shape that gives the nav its capability filter, its fail-closed
"not loaded yet" behaviour, and route-level coherence for direct navigation.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = PROJECT_ROOT / "frontend" / "src"
SHELL = FRONTEND_SRC / "components" / "shell"
ROOMS_TS = SHELL / "rooms.ts"
RAIL_TSX = SHELL / "Rail.tsx"
ROOM_SHELL_TSX = SHELL / "RoomShell.tsx"
REQUIRE_CAPABILITY_TSX = SHELL / "RequireCapability.tsx"
MOBILE_NAV_TSX = SHELL / "MobileRoomNav.tsx"
APP_TSX = FRONTEND_SRC / "App.tsx"
CAPABILITIES_TSX = FRONTEND_SRC / "lib" / "capabilities.tsx"
STOCKS_TSX = FRONTEND_SRC / "pages" / "Stocks.tsx"

# Room path -> capability; tab path -> capability. Mirrors the orchestrator's
# fixed mapping: Today has none, Portfolio's tabs are split four ways, Agents'
# Reports tab differs from the room itself, Settings' Data Sources tab differs
# from the room itself.
EXPECTED_PATH_CAPABILITIES = {
    "/today": None,
    "/portfolio": "portfolio:read",
    "/portfolio/watchlist": "watchlist:read",
    "/portfolio/paper": "paper:read",
    "/portfolio/alerts": "alerts:read",
    "/discover": "discover:read",
    "/agents": "agents:read",
    "/agents/reports": "reports:read",
    "/settings": "settings:read",
    "/settings/data-sources": "datasources:read",
}


def _read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(PROJECT_ROOT)}"
    return path.read_text(encoding="utf-8")


def test_capability_provider_is_mounted_in_the_signed_in_shell():
    """App.tsx 挂载 CapabilityProvider，useCapabilities() 在已登录外壳内才是活的"""
    src = _read(APP_TSX)
    assert "import { CapabilityProvider } from '@/lib/capabilities'" in src
    assert "<CapabilityProvider>" in src and "</CapabilityProvider>" in src


def test_every_room_and_tab_path_maps_to_the_decided_capability():
    """rooms.ts 里每个房间/标签页的 to 与 capability 精确匹配契约表"""
    src = _read(ROOMS_TS)
    # Pull every `to: '<path>'` / `capability: '<name>'` pair that appears as
    # sibling keys of one object literal by scanning object-literal chunks.
    objects = re.findall(r"\{[^{}]*\}", src, re.DOTALL)
    seen = {}
    for obj in objects:
        to_match = re.search(r"to:\s*'([^']+)'", obj)
        if not to_match:
            continue
        cap_match = re.search(r"capability:\s*'([^']+)'", obj)
        seen[to_match.group(1)] = cap_match.group(1) if cap_match else None

    for path, expected_cap in EXPECTED_PATH_CAPABILITIES.items():
        assert path in seen, f"rooms.ts has no entry for {path}"
        assert seen[path] == expected_cap, (
            f"{path} expected capability {expected_cap!r}, found {seen[path]!r}"
        )


def test_visible_rooms_fails_closed_before_capabilities_load():
    """visibleRooms 用 capabilities.has 过滤，未加载时 has() 恒为 false（见 capabilities.tsx），因此门控项不会先出现再消失"""
    rooms_src = _read(ROOMS_TS)
    assert re.search(
        r"filter\(r => !r\.capability \|\| capabilities\.has\(r\.capability\)\)",
        rooms_src,
    )
    assert re.search(
        r"filter\(t => !t\.capability \|\| capabilities\.has\(t\.capability\)\)",
        rooms_src,
    )
    caps_src = _read(CAPABILITIES_TSX)
    # capabilities.tsx's own contract: has() is false for everything until
    # `list` is populated, and `list` starts empty.
    assert "const NONE: readonly string[] = []" in caps_src
    assert "const [list, setList] = useState<readonly string[]>(NONE)" in caps_src


def test_rail_and_mobile_nav_render_only_visible_rooms():
    """Rail（桌面）与 MobileRoomNav（移动端）都渲染 visibleRooms 过滤后的列表，而不是原始 ROOMS"""
    rail_src = _read(RAIL_TSX)
    assert "visibleRooms(ROOMS, capabilities)" in rail_src
    assert "{rooms.map(" in rail_src

    mobile_src = _read(MOBILE_NAV_TSX)
    assert "visibleRooms(ROOMS, capabilities)" in mobile_src
    assert "{rooms.map(" in mobile_src
    # Must call useCapabilities() itself: it has to run inside
    # CapabilityProvider's own subtree, which App() cannot do for code in
    # App()'s own function body (see the file's doc comment).
    assert "useCapabilities()" in mobile_src


def test_room_shell_hides_a_gated_room_header_and_filters_its_tabs():
    """RoomShell：当前房间未授权时不渲染头部，标签页按各自 capability 过滤"""
    src = _read(ROOM_SHELL_TSX)
    assert "const roomVisible = !room.capability || capabilities.has(room.capability)" in src
    assert "if (!roomVisible) return <>{children}</>" in src
    assert "room.tabs?.filter(t => !t.capability || capabilities.has(t.capability))" in src


def test_require_capability_waits_instead_of_guessing():
    """RequireCapability：未加载时等待（渲染 fallback），已加载且未授权才重定向，不在信息不全时下结论"""
    src = _read(REQUIRE_CAPABILITY_TSX)
    assert "if (!capability) return children" in src
    assert "if (!capabilities.loaded) return routeFallback" in src
    assert "if (!capabilities.has(capability)) return <Navigate to=\"/today\" replace />" in src
    # The loaded check must come before the has() check, so an unresolved
    # probe can never be misread as "confirmed absent".
    loaded_idx = src.index("capabilities.loaded")
    has_idx = src.index("capabilities.has(capability)")
    assert loaded_idx < has_idx


def test_every_gated_room_and_tab_route_is_wrapped_in_require_capability():
    """App.tsx 的路由表：每条有 capability 要求的路径都包了 RequireCapability，用 capabilityForPath 取值而不是硬编码"""
    src = _read(APP_TSX)
    assert "import RequireCapability from '@/components/shell/RequireCapability'" in src
    assert "capabilityForPath" in src
    for path, expected_cap in EXPECTED_PATH_CAPABILITIES.items():
        route_match = re.search(
            r'<Route path="' + re.escape(path) + r'" element=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}\s*/>',
            src,
        )
        assert route_match, f"no <Route> found for {path}"
        element_src = route_match.group(1)
        if expected_cap is None:
            assert "RequireCapability" not in element_src, f"{path} should not be capability-gated"
        else:
            assert "RequireCapability" in element_src, f"{path} should be wrapped in RequireCapability"
            assert f"capabilityForPath('{path}')" in element_src


def test_stocks_page_skips_agents_fetch_without_the_capability():
    """Stocks.tsx 的 loadConfigAsync：/agents 与 /providers/services、/channels 都按各自 capability 门控"""
    src = _read(STOCKS_TSX)
    assert "const capabilities = useCapabilities()" in src
    assert "capabilities.has('agents:read') ? fetchAPI<AgentConfig[]>('/agents')" in src
    assert "capabilities.has('settings:read') ? fetchAPI<AIService[]>('/providers/services')" in src
    assert "capabilities.has('settings:read') ? fetchAPI<NotifyChannel[]>('/channels')" in src


def test_stocks_page_defers_config_load_until_capabilities_have_settled():
    """loadConfigAsync 不在挂载时立即调用（capabilities 还未加载），而是等 capabilities.loaded 变 true 后单独触发一次"""
    src = _read(STOCKS_TSX)
    mount_effect = re.search(r"useEffect\(\(\) => \{ load\(\); loadPortfolio\(\).*?\}, \[\]\)", src)
    assert mount_effect, "mount effect not found"
    assert "loadConfigAsync" not in mount_effect.group(0), (
        "loadConfigAsync must not run from the unconditional mount effect"
    )
    assert re.search(
        r"useEffect\(\(\) => \{\s*if \(!capabilities\.loaded\) return\s*loadConfigAsync\(\)",
        src,
    ), "a dedicated effect keyed on capabilities.loaded must call loadConfigAsync"
