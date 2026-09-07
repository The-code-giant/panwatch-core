from src.web.static_site import accepts_gzip, resolve_static_page


def test_gzip_negotiation():
    assert accepts_gzip("gzip, deflate, br")
    assert accepts_gzip("br, gzip;q=0.5")
    assert not accepts_gzip("gzip;q=0")
    assert not accepts_gzip("br")
    assert not accepts_gzip("gzip;q=invalid")
    assert not accepts_gzip("")


def test_public_pages_and_app_fallback(tmp_path):
    (tmp_path / "index.html").write_text("home")
    (tmp_path / "app-shell.html").write_text("app")
    (tmp_path / "pricing").mkdir()
    (tmp_path / "pricing" / "index.html").write_text("pricing")
    assert resolve_static_page(str(tmp_path), "") == (tmp_path / "index.html", 200)
    assert resolve_static_page(str(tmp_path), "pricing") == (tmp_path / "pricing" / "index.html", 200)
    assert resolve_static_page(str(tmp_path), "pricing/") == (tmp_path / "pricing" / "index.html", 200)
    assert resolve_static_page(str(tmp_path), "login") == (tmp_path / "app-shell.html", 200)
    assert resolve_static_page(str(tmp_path), "agents") == (tmp_path / "app-shell.html", 200)


def test_missing_research_and_assets(tmp_path):
    (tmp_path / "404.html").write_text("not found")
    assert resolve_static_page(str(tmp_path), "research/missing") == (tmp_path / "404.html", 404)
    assert resolve_static_page(str(tmp_path), "assets/missing.js") == (None, 404)


def test_traversal_and_symlinks_cannot_escape(tmp_path):
    assert resolve_static_page(str(tmp_path), "../secret") == (None, 404)
    assert resolve_static_page(str(tmp_path), "/etc/passwd") == (None, 404)
    (tmp_path / "outside").symlink_to("/etc")
    assert resolve_static_page(str(tmp_path), "outside/passwd") == (None, 404)
