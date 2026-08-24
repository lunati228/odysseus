"""Static contract tests for the Standard/Privacy workspace control."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
LOGIN = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _control_markup(source: str) -> str:
    match = re.search(
        r'<aside\b[^>]*id="privacy-workspace-control"[\s\S]*?</aside>',
        source,
    )
    assert match, "privacy workspace control is missing"
    return match.group(0)


def test_control_is_top_level_and_outside_collapsible_navigation():
    index_control = INDEX.index('id="privacy-workspace-control"')
    assert INDEX.index("<body>") < index_control < INDEX.index('id="app-loader"')
    assert index_control < INDEX.index('id="icon-rail"')
    assert index_control < INDEX.index('id="sidebar"')

    login_control = LOGIN.index('id="privacy-workspace-control"')
    assert LOGIN.index("<body>") < login_control < LOGIN.index('<main class="card">')


def test_both_pages_expose_native_accessible_workspace_controls():
    for source in (INDEX, LOGIN):
        control = _control_markup(source)
        assert 'aria-label="Workspace status and switcher"' in control
        assert 'id="privacy-workspace-label"' in control
        assert 'aria-current="page"' in control
        assert 'id="privacy-workspace-transport"' in control
        assert 'role="status"' in control
        assert 'aria-live="polite"' in control

        button = re.search(
            r'<button\b[^>]*id="privacy-workspace-switch"[^>]*>', control
        )
        assert button, "workspace switch must be a native button"
        assert 'type="button"' in button.group(0)
        assert 'aria-label="Switch workspace"' in button.group(0)
        assert "disabled" in button.group(0)


def test_both_pages_load_the_dedicated_module():
    script = '<script type="module" src="/static/js/privacyWorkspace.js"></script>'
    assert script in INDEX
    assert script in LOGIN


def test_main_styles_keep_control_fixed_focusable_and_mobile_reachable():
    assert re.search(
        r"\.privacy-workspace-control\s*\{[^}]*position:\s*fixed",
        STYLE,
        re.S,
    )
    assert re.search(
        r"\.privacy-workspace-switch:focus-visible\s*\{[^}]*outline:",
        STYLE,
        re.S,
    )
    mobile = re.search(r"@media\s*\(max-width:\s*768px\)\s*\{(?P<body>[\s\S]+)", STYLE)
    assert mobile
    assert ".privacy-workspace-control" in mobile.group("body")
    assert re.search(
        r"\.privacy-workspace-current\s*\{[^}]*grid-area:\s*identity",
        STYLE,
        re.S,
    )
    assert ".privacy-workspace-switch-prefix" in mobile.group("body")
    assert re.search(
        r"\.privacy-workspace-switch\s*\{[^}]*min-height:\s*44px",
        mobile.group("body"),
        re.S,
    )


def test_login_has_equivalent_scoped_focus_and_mobile_styles():
    assert re.search(
        r"\.privacy-workspace-control\s*\{[^}]*position:\s*fixed",
        LOGIN,
        re.S,
    )
    assert re.search(
        r"\.privacy-workspace-switch:focus-visible\s*\{[^}]*outline:",
        LOGIN,
        re.S,
    )
    assert re.search(
        r"@media\s*\(max-width:\s*768px\)[\s\S]*"
        r"\.privacy-workspace-switch\s*\{[^}]*min-height:\s*44px",
        LOGIN,
        re.S,
    )
