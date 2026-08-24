// Standard/Privacy workspace status and navigation control.
// This module deliberately owns no chat state. Switching is a root-level,
// full-page navigation only after the backend status contract is validated.

const STATUS_ENDPOINT = '/api/privacy/status';
const PROFILE_LABELS = {
  standard: 'Standard Workspace',
  privacy: 'Privacy Workspace',
};

function getElements(documentRef) {
  if (!documentRef || typeof documentRef.getElementById !== 'function') return null;
  const elements = {
    control: documentRef.getElementById('privacy-workspace-control'),
    label: documentRef.getElementById('privacy-workspace-label'),
    transport: documentRef.getElementById('privacy-workspace-transport'),
    button: documentRef.getElementById('privacy-workspace-switch'),
    targetLabel: documentRef.getElementById('privacy-workspace-switch-label'),
  };
  return Object.values(elements).every(Boolean) ? elements : null;
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

function showUnknown(elements) {
  elements.control.dataset.profile = 'unknown';
  elements.control.dataset.state = 'unknown';
  elements.label.textContent = 'Unknown';
  elements.transport.hidden = false;
  elements.transport.textContent = 'Workspace status unavailable';
  elements.button.disabled = true;
  elements.button.setAttribute('aria-label', 'Switch workspace');
  elements.targetLabel.textContent = 'Unavailable';
}

function showStatus(elements, status, navigate) {
  elements.control.dataset.profile = status.profile;
  elements.label.textContent = status.label;
  elements.button.disabled = false;
  elements.button.setAttribute('aria-label', `Switch to ${status.targetLabel}`);
  elements.targetLabel.textContent = status.targetLabel;

  if (status.profile === 'privacy') {
    elements.control.dataset.state = status.transport.ready ? 'ready' : 'unavailable';
    elements.transport.hidden = false;
    elements.transport.textContent = `${status.transport.label} ${status.transport.ready ? 'ready' : 'unavailable'}`;
  } else {
    elements.control.dataset.state = 'ready';
    elements.transport.textContent = '';
    elements.transport.hidden = true;
  }

  elements.button.addEventListener('click', () => navigate(status.counterpartUrl));
}

export async function mountPrivacyWorkspace({
  document: documentRef,
  fetchImpl,
  navigate,
} = {}) {
  const elements = getElements(documentRef);
  if (!elements || typeof fetchImpl !== 'function' || typeof navigate !== 'function') return null;

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
    showStatus(elements, status, navigate);
    return status;
  } catch (_) {
    showUnknown(elements);
    return null;
  }
}

function mountBrowserControl() {
  mountPrivacyWorkspace({
    document,
    fetchImpl: window.fetch.bind(window),
    navigate: (url) => window.location.assign(url),
  });
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountBrowserControl, { once: true });
  } else {
    mountBrowserControl();
  }
}
