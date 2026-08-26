"""Executable behavior tests for static/js/privacyWorkspace.js."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "privacyWorkspace.js"
HAS_NODE = shutil.which("node") is not None


def _run_node(case_body: str) -> dict:
    module_url = json.dumps(MODULE.as_uri())
    script = f"""
import {{
  browserTimezoneHeaders,
  mountPrivacyWorkspace,
  privacyPresenceEndpoint,
  startPrivacyUiPresence,
}} from {module_url};

class FakeElement {{
  constructor() {{
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.dataset = {{}};
    this.attributes = {{}};
    this.listeners = {{}};
    this.queries = {{}};
  }}
  setAttribute(name, value) {{ this.attributes[name] = String(value); }}
  removeAttribute(name) {{ delete this.attributes[name]; }}
  addEventListener(type, callback) {{ this.listeners[type] = callback; }}
  querySelector(selector) {{ return this.queries[selector] || null; }}
  click() {{
    if (!this.disabled && this.listeners.click) this.listeners.click({{ type: 'click' }});
  }}
}}

const ids = [
  'privacy-workspace-control',
  'privacy-workspace-label',
  'privacy-workspace-transport',
  'privacy-workspace-switch',
  'privacy-workspace-switch-label',
];
const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement()]));
const mirrorElements = {{
  control: new FakeElement(),
  label: new FakeElement(),
  transport: new FakeElement(),
  button: new FakeElement(),
  targetLabel: new FakeElement(),
}};
mirrorElements.control.queries['[data-privacy-workspace-label]'] = mirrorElements.label;
mirrorElements.control.queries['[data-privacy-workspace-transport]'] = mirrorElements.transport;
mirrorElements.control.queries['[data-privacy-workspace-switch]'] = mirrorElements.button;
mirrorElements.control.queries['[data-privacy-workspace-switch-label]'] = mirrorElements.targetLabel;
const fakeDocument = {{
  getElementById: (id) => elements[id] || null,
  querySelectorAll: (selector) => selector === '[data-privacy-workspace-control]'
    ? [mirrorElements.control] : [],
}};
const storageTrap = new Proxy({{}}, {{
  get() {{ throw new Error('workspace module must not access browser storage'); }}
}});
globalThis.localStorage = storageTrap;
globalThis.sessionStorage = storageTrap;
let navigations = [];
const navigate = (url) => navigations.push(url);

{case_body}
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_privacy_ready_status_is_textual_and_switches_to_exact_standard_root():
    result = _run_node(
        """
const payload = {
  profile: 'privacy',
  label: 'Privacy Workspace',
  counterpart_url: 'http://127.0.0.1:7000/',
  transport: { required: true, ready: true, label: 'Tor' },
  data_isolated: true,
  session_migration: false,
  disabled_capabilities: ['cloud-models'],
};
let requests = [];
const fetchImpl = async (url, options) => {
  requests.push({ url, options });
  return { ok: true, json: async () => payload };
};
await mountPrivacyWorkspace({ document: fakeDocument, fetchImpl, navigate });
elements['privacy-workspace-switch'].click();
console.log(JSON.stringify({
  label: elements['privacy-workspace-label'].textContent,
  transport: elements['privacy-workspace-transport'].textContent,
  profile: elements['privacy-workspace-control'].dataset.profile,
  state: elements['privacy-workspace-control'].dataset.state,
  buttonDisabled: elements['privacy-workspace-switch'].disabled,
  buttonLabel: elements['privacy-workspace-switch'].attributes['aria-label'],
  targetLabel: elements['privacy-workspace-switch-label'].textContent,
  navigations,
  requests,
}));
"""
    )

    assert result["label"] == "Privacy Workspace"
    assert result["transport"] == "Tor ready"
    assert result["profile"] == "privacy"
    assert result["state"] == "ready"
    assert result["buttonDisabled"] is False
    assert result["buttonLabel"] == "Switch to Standard Workspace"
    assert result["targetLabel"] == "Standard Workspace"
    assert result["navigations"] == ["http://127.0.0.1:7000/"]
    assert result["requests"] == [
        {
            "url": "/api/privacy/status",
            "options": {
                "method": "GET",
                "credentials": "same-origin",
                "cache": "no-store",
                "headers": {"Accept": "application/json"},
            },
        }
    ]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_compact_sidebar_mirror_tracks_status_and_switches_workspace():
    result = _run_node(
        """
const payload = {
  profile: 'privacy',
  label: 'Privacy Workspace',
  counterpart_url: 'http://127.0.0.1:7000/',
  transport: { required: true, ready: true, label: 'Tor' },
  data_isolated: true,
  session_migration: false,
  disabled_capabilities: [],
};
await mountPrivacyWorkspace({
  document: fakeDocument,
  fetchImpl: async () => ({ ok: true, json: async () => payload }),
  navigate,
});
mirrorElements.button.click();
console.log(JSON.stringify({
  profile: mirrorElements.control.dataset.profile,
  state: mirrorElements.control.dataset.state,
  label: mirrorElements.label.textContent,
  transport: mirrorElements.transport.textContent,
  buttonLabel: mirrorElements.button.attributes['aria-label'],
  title: mirrorElements.button.attributes.title,
  navigations,
}));
"""
    )

    assert result == {
        "profile": "privacy",
        "state": "ready",
        "label": "Privacy Workspace",
        "transport": "Tor ready",
        "buttonLabel": "Switch to Standard Workspace",
        "title": (
            "Privacy Workspace · Tor ready · Switch to Standard Workspace"
        ),
        "navigations": ["http://127.0.0.1:7000/"],
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_privacy_unavailable_status_is_non_color_only_but_switch_remains_available():
    result = _run_node(
        """
const payload = {
  profile: 'privacy',
  label: 'Privacy Workspace',
  counterpart_url: 'http://127.0.0.1:7000/',
  transport: { required: true, ready: false, label: 'Tor' },
  data_isolated: true,
  session_migration: false,
  disabled_capabilities: [],
};
await mountPrivacyWorkspace({
  document: fakeDocument,
  fetchImpl: async () => ({ ok: true, json: async () => payload }),
  navigate,
});
elements['privacy-workspace-switch'].click();
console.log(JSON.stringify({
  transport: elements['privacy-workspace-transport'].textContent,
  state: elements['privacy-workspace-control'].dataset.state,
  buttonDisabled: elements['privacy-workspace-switch'].disabled,
  navigations,
}));
"""
    )

    assert result == {
        "transport": "Tor unavailable",
        "state": "unavailable",
        "buttonDisabled": False,
        "navigations": ["http://127.0.0.1:7000/"],
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_standard_status_hides_transport_detail_and_targets_privacy_root():
    result = _run_node(
        """
const payload = {
  profile: 'standard',
  label: 'Standard Workspace',
  counterpart_url: 'http://127.0.0.1:7001/',
  transport: { required: false, ready: true, label: 'Direct' },
  data_isolated: true,
  session_migration: false,
  disabled_capabilities: [],
};
await mountPrivacyWorkspace({
  document: fakeDocument,
  fetchImpl: async () => ({ ok: true, json: async () => payload }),
  navigate,
});
elements['privacy-workspace-switch'].click();
console.log(JSON.stringify({
  label: elements['privacy-workspace-label'].textContent,
  transportHidden: elements['privacy-workspace-transport'].hidden,
  targetLabel: elements['privacy-workspace-switch-label'].textContent,
  navigations,
}));
"""
    )

    assert result == {
        "label": "Standard Workspace",
        "transportHidden": True,
        "targetLabel": "Privacy Workspace",
        "navigations": ["http://127.0.0.1:7001/"],
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_privacy_profile_does_not_read_or_send_browser_timezone():
    result = _run_node(
        """
elements['privacy-workspace-control'].dataset.profile = 'privacy';
let clockRead = false;
let zoneRead = false;
const headers = browserTimezoneHeaders({
  document: fakeDocument,
  now: () => { clockRead = true; throw new Error('privacy must not read clock offset'); },
  resolveTimeZone: () => { zoneRead = true; throw new Error('privacy must not read timezone'); },
});
console.log(JSON.stringify({ headers, clockRead, zoneRead }));
"""
    )

    assert result == {"headers": {}, "clockRead": False, "zoneRead": False}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_standard_profile_keeps_browser_timezone_headers():
    result = _run_node(
        """
elements['privacy-workspace-control'].dataset.profile = 'standard';
const headers = browserTimezoneHeaders({
  document: fakeDocument,
  now: () => ({ getTimezoneOffset: () => -120 }),
  resolveTimeZone: () => 'Europe/Berlin',
});
console.log(JSON.stringify(headers));
"""
    )

    assert result == {"X-Tz-Offset": "120", "X-Tz-Name": "Europe/Berlin"}


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_privacy_presence_uses_only_the_fixed_numeric_loopback_endpoint():
    result = _run_node(
        """
const privacy = { profile: 'privacy' };
const standard = { profile: 'standard' };
const goodLocation = { protocol: 'http:', hostname: '127.0.0.1', port: '7001' };
const wrongHost = { protocol: 'http:', hostname: 'localhost', port: '7001' };
console.log(JSON.stringify({
  privacy: privacyPresenceEndpoint(privacy, goodLocation),
  standard: privacyPresenceEndpoint(standard, goodLocation),
  wrongHost: privacyPresenceEndpoint(privacy, wrongHost),
}));
"""
    )

    assert result == {
        "privacy": "/api/privacy/ui-presence",
        "standard": None,
        "wrongHost": None,
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_privacy_presence_opens_one_persistent_channel_without_focus_tracking():
    result = _run_node(
        """
const requests = [];
const fetchImpl = (url, options) => {
  requests.push({ url, options: { ...options, signal: Boolean(options.signal) } });
  return new Promise(() => {});
};
class FakeAbortController {
  constructor() { this.signal = {}; }
  abort() {}
}
const listeners = [];
const handle = startPrivacyUiPresence({
  status: { profile: 'privacy' },
  fetchImpl,
  AbortControllerImpl: FakeAbortController,
  locationRef: { protocol: 'http:', hostname: '127.0.0.1', port: '7001' },
  addEventListenerImpl: (type) => listeners.push(type),
  setTimeoutImpl: () => 1,
  clearTimeoutImpl: () => {},
});
console.log(JSON.stringify({
  requests,
  listeners,
  started: Boolean(handle),
}));
"""
    )

    assert result == {
        "requests": [
            {
                "url": "/api/privacy/ui-presence",
                "options": {
                    "method": "POST",
                    "credentials": "same-origin",
                    "cache": "no-store",
                    "headers": {
                        "Accept": "text/event-stream",
                        "X-Odysseus-UI-Presence": "1",
                    },
                    "signal": True,
                },
            }
        ],
        "listeners": ["online", "pageshow", "resume"],
        "started": True,
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize(
    "response_expression",
    [
        "Promise.reject(new Error('offline'))",
        "Promise.resolve({ ok: false, status: 503, json: async () => ({}) })",
        "Promise.resolve({ ok: true, json: async () => ({ profile: 'privacy' }) })",
        "Promise.resolve({ ok: true, json: async () => ({"
        " profile: 'privacy', label: 'Privacy Workspace',"
        " counterpart_url: 'http://127.0.0.1:7000/?session_id=secret#chat',"
        " transport: { required: true, ready: true, label: 'Tor' },"
        " data_isolated: true, session_migration: false, disabled_capabilities: []"
        " }) })",
    ],
)
def test_unavailable_or_malformed_status_fails_closed_without_guessed_navigation(
    response_expression,
):
    result = _run_node(
        f"""
const fetchImpl = () => {response_expression};
await mountPrivacyWorkspace({{ document: fakeDocument, fetchImpl, navigate }});
elements['privacy-workspace-switch'].click();
console.log(JSON.stringify({{
  label: elements['privacy-workspace-label'].textContent,
  transport: elements['privacy-workspace-transport'].textContent,
  state: elements['privacy-workspace-control'].dataset.state,
  buttonDisabled: elements['privacy-workspace-switch'].disabled,
  targetLabel: elements['privacy-workspace-switch-label'].textContent,
  navigations,
}}));
"""
    )

    assert result == {
        "label": "Unknown",
        "transport": "Workspace status unavailable",
        "state": "unknown",
        "buttonDisabled": True,
        "targetLabel": "Unavailable",
        "navigations": [],
    }


def test_module_never_reads_or_writes_chat_session_or_browser_storage_state():
    source = MODULE.read_text(encoding="utf-8")
    forbidden = (
        "localStorage",
        "sessionStorage",
        "currentSession",
        "session_id",
        "location.search",
        "location.hash",
    )
    for token in forbidden:
        assert token not in source
