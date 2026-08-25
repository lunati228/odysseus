"""Static contract tests for the Standard/Privacy workspace control."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
LOGIN = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def _control_markup(source: str) -> str:
    match = re.search(
        r'<(?P<tag>aside|div)\b[^>]*id="privacy-workspace-control"[\s\S]*?'
        r'</(?P=tag)>',
        source,
    )
    assert match, "privacy workspace control is missing"
    return match.group(0)


def test_main_control_is_compact_and_immediately_above_rail_settings():
    index_control = INDEX.index('id="privacy-workspace-control"')
    assert INDEX.index('id="icon-rail"') < index_control
    assert index_control < INDEX.index('id="rail-settings"')
    between = INDEX[index_control:INDEX.index('id="rail-settings"')]
    assert "privacy-workspace-switch" in between
    assert "privacy-workspace-status-dot" in between

    mirror = INDEX.index('data-privacy-workspace-control')
    assert INDEX.index('class="user-bar-actions"') < mirror
    assert mirror < INDEX.index('id="user-bar-settings"')


def test_login_control_stays_top_level():
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


def test_main_styles_make_control_compact_focusable_and_stateful():
    compact = re.search(
        r"\.privacy-workspace-compact\s*\{(?P<body>[^}]*)}", STYLE, re.S
    )
    assert compact
    assert "position: fixed" not in compact.group("body")
    assert re.search(
        r"\.privacy-workspace-switch:focus-visible\s*\{[^}]*outline:",
        STYLE,
        re.S,
    )
    assert re.search(
        r"\.privacy-workspace-status-dot\s*\{[^}]*border-radius:\s*50%",
        STYLE,
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
