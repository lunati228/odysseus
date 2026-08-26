// Standard/Privacy workspace status and navigation control.
// This module deliberately owns no chat state. Switching is a root-level,
// full-page navigation only after the backend status contract is validated.

const STATUS_ENDPOINT = '/api/privacy/status';
const PRESENCE_ENDPOINT = '/api/privacy/ui-presence';
const PROFILE_LABELS = {
  standard: 'Standard Workspace',
  privacy: 'Privacy Workspace',
};

function getElements(documentRef) {
  if (!documentRef || typeof documentRef.getElementById !== 'function') return null;
  const primary = {
    control: documentRef.getElementById('privacy-workspace-control'),
    label: documentRef.getElementById('privacy-workspace-label'),
    transport: documentRef.getElementById('privacy-workspace-transport'),
    button: documentRef.getElementById('privacy-workspace-switch'),
    targetLabel: documentRef.getElementById('privacy-workspace-switch-label'),
  };
  if (!Object.values(primary).every(Boolean)) return null;

  const groups = [primary];
  if (typeof documentRef.querySelectorAll === 'function') {
    for (const control of documentRef.querySelectorAll('[data-privacy-workspace-control]')) {
      if (!control || typeof control.querySelector !== 'function') continue;
      const mirror = {
        control,
        label: control.querySelector('[data-privacy-workspace-label]'),
        transport: control.querySelector('[data-privacy-workspace-transport]'),
        button: control.querySelector('[data-privacy-workspace-switch]'),
        targetLabel: control.querySelector('[data-privacy-workspace-switch-label]'),
      };
      if (Object.values(mirror).every(Boolean)) groups.push(mirror);
    }
  }
  return groups;
}

export function browserTimezoneHeaders({
  document: documentRef,
  now = () => new Date(),
  resolveTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || '',
} = {}) {
  const control = documentRef?.getElementById?.('privacy-workspace-control');
  // Fail closed while the profile is unknown. Browser timezone metadata is
  // sent only after the backend has positively identified Standard Workspace.
  if (control?.dataset?.profile !== 'standard') return {};

  try {
    return {
      'X-Tz-Offset': String(-now().getTimezoneOffset()),
      'X-Tz-Name': String(resolveTimeZone() || ''),
    };
  } catch (_) {
    return {};
  }
}

function validatedCounterpart(rawUrl, profile) {
  if (typeof rawUrl !== 'string' || !rawUrl) return null;
  try {
    const url = new URL(rawUrl);
    const expectedPort = profile === 'privacy' ? '7000' : '7001';
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') return null;
    if (url.port !== expectedPort || url.pathname !== '/') return null;
    if (url.username || url.password || url.search || url.hash) return null;
    return url.href;
  } catch (_) {
    return null;
  }
}

export function normalizePrivacyWorkspaceStatus(payload) {
  if (!payload || typeof payload !== 'object') return null;
  if (!Object.hasOwn(PROFILE_LABELS, payload.profile)) return null;
  if (typeof payload.label !== 'string' || !payload.label.trim()) return null;
  if (payload.data_isolated !== true || payload.session_migration !== false) return null;
  if (!Array.isArray(payload.disabled_capabilities)) return null;
  if (!payload.transport || typeof payload.transport !== 'object') return null;
  if (typeof payload.transport.required !== 'boolean') return null;
  if (typeof payload.transport.ready !== 'boolean') return null;
  if (typeof payload.transport.label !== 'string' || !payload.transport.label.trim()) return null;

  const counterpartUrl = validatedCounterpart(payload.counterpart_url, payload.profile);
  if (!counterpartUrl) return null;

  return {
    profile: payload.profile,
    label: payload.label.trim(),
    counterpartUrl,
    targetLabel: PROFILE_LABELS[payload.profile === 'privacy' ? 'standard' : 'privacy'],
    transport: {
      required: payload.transport.required,
      ready: payload.transport.ready,
      label: payload.transport.label.trim(),
    },
  };
}

export function privacyPresenceEndpoint(status, locationRef) {
  if (!status || status.profile !== 'privacy' || !locationRef) return null;
  if (
    locationRef.protocol !== 'http:'
    || locationRef.hostname !== '127.0.0.1'
    || locationRef.port !== '7001'
  ) return null;
  return PRESENCE_ENDPOINT;
}

export function startPrivacyUiPresence({
  status,
  fetchImpl = globalThis.fetch?.bind(globalThis),
  AbortControllerImpl = globalThis.AbortController,
  locationRef = globalThis.location,
  addEventListenerImpl = globalThis.addEventListener?.bind(globalThis),
  setTimeoutImpl = globalThis.setTimeout?.bind(globalThis),
  clearTimeoutImpl = globalThis.clearTimeout?.bind(globalThis),
} = {}) {
  const endpoint = privacyPresenceEndpoint(status, locationRef);
  if (
    !endpoint
    || typeof fetchImpl !== 'function'
    || typeof AbortControllerImpl !== 'function'
  ) return null;

  let controller = null;
  let reconnectTimer = null;
  let connecting = false;
  let stopped = false;

  const clearReconnect = () => {
    if (reconnectTimer !== null && typeof clearTimeoutImpl === 'function') {
      clearTimeoutImpl(reconnectTimer);
    }
    reconnectTimer = null;
  };

  const scheduleReconnect = () => {
    if (stopped || reconnectTimer !== null || typeof setTimeoutImpl !== 'function') return;
    reconnectTimer = setTimeoutImpl(() => {
      reconnectTimer = null;
      connect();
    }, 2000);
  };

  const connect = async () => {
    if (stopped || connecting) return;
    connecting = true;
    controller = new AbortControllerImpl();
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          Accept: 'text/event-stream',
          'X-Odysseus-UI-Presence': '1',
        },
        signal: controller.signal,
      });
      if (!response?.ok || !response.body?.getReader) throw new Error('presence unavailable');
      clearReconnect();
      const reader = response.body.getReader();
      while (!stopped) {
        const result = await reader.read();
        if (result.done) break;
      }
    } catch (_) {
      // A closed tab aborts this request. Transient failures reconnect while
      // the page still exists, without treating focus/visibility as absence.
    } finally {
      connecting = false;
      controller = null;
      scheduleReconnect();
    }
  };

  if (typeof addEventListenerImpl === 'function') {
    for (const eventName of ['online', 'pageshow', 'resume']) {
      addEventListenerImpl(eventName, connect);
    }
  }
  connect();

  return {
    stop() {
      stopped = true;
      clearReconnect();
      if (controller && typeof controller.abort === 'function') controller.abort();
    },
  };
}

function showUnknown(elementGroups) {
  for (const elements of elementGroups) {
    elements.control.dataset.profile = 'unknown';
    elements.control.dataset.state = 'unknown';
    elements.label.textContent = 'Unknown';
    elements.transport.hidden = false;
    elements.transport.textContent = 'Workspace status unavailable';
    elements.button.disabled = true;
    elements.button.setAttribute('aria-label', 'Switch workspace');
    elements.button.setAttribute('title', 'Workspace status unavailable');
    elements.targetLabel.textContent = 'Unavailable';
  }
}

function showStatus(elementGroups, status, navigate) {
  for (const elements of elementGroups) {
    elements.control.dataset.profile = status.profile;
    elements.label.textContent = status.label;
    elements.button.disabled = false;
    elements.button.setAttribute('aria-label', `Switch to ${status.targetLabel}`);
    elements.targetLabel.textContent = status.targetLabel;

    let transportText = '';
    if (status.profile === 'privacy') {
      elements.control.dataset.state = status.transport.ready ? 'ready' : 'unavailable';
      elements.transport.hidden = false;
      transportText = `${status.transport.label} ${status.transport.ready ? 'ready' : 'unavailable'}`;
      elements.transport.textContent = transportText;
    } else {
      elements.control.dataset.state = 'ready';
      elements.transport.textContent = '';
      elements.transport.hidden = true;
      transportText = 'Direct connection';
    }

    elements.button.setAttribute(
      'title', `${status.label} · ${transportText} · Switch to ${status.targetLabel}`
    );
    elements.button.addEventListener('click', () => navigate(status.counterpartUrl));
  }
}

export async function mountPrivacyWorkspace({
  document: documentRef,
  fetchImpl,
  navigate,
} = {}) {
  const elementGroups = getElements(documentRef);
  if (!elementGroups || typeof fetchImpl !== 'function' || typeof navigate !== 'function') return null;

  try {
    const response = await fetchImpl(STATUS_ENDPOINT, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response || !response.ok) throw new Error('status unavailable');
    const status = normalizePrivacyWorkspaceStatus(await response.json());
    if (!status) throw new Error('invalid status');
    showStatus(elementGroups, status, navigate);
    return status;
  } catch (_) {
    showUnknown(elementGroups);
    return null;
  }
}

async function mountBrowserControl() {
  const status = await mountPrivacyWorkspace({
    document,
    fetchImpl: window.fetch.bind(window),
    navigate: (url) => window.location.assign(url),
  });
  startPrivacyUiPresence({ status });
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountBrowserControl, { once: true });
  } else {
    mountBrowserControl();
  }
}
