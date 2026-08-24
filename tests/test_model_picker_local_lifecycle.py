"""Static UI contracts for manager-owned local model switching.

The real browser path is covered by the fork E2E pass.  These assertions keep
the critical lifecycle wiring from silently regressing in ordinary pytest runs.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PICKER = (ROOT / "static/js/modelPicker.js").read_text(encoding="utf-8")
HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")


def test_picker_lists_and_activates_manager_owned_models():
    assert "${API_BASE}/api/local-models" in PICKER
    assert "/api/local-models/${encodeURIComponent(model.localKey)}/activation" in PICKER
    assert "method: 'POST'" in PICKER
    assert "credentials: 'same-origin'" in PICKER
    assert "normalized.state !== 'READY'" in PICKER
    assert "normalized.activeModelKey !== model.localKey" in PICKER


def test_picker_waits_for_activation_before_selecting_chat_model():
    activation = PICKER.index("async function _activateManagedLocal")
    post = PICKER.index("/activation`,", activation)
    readiness = PICKER.index("normalized.state !== 'READY'", post)
    pick = PICKER.index("await _pick(loaded)", readiness)

    assert activation < post < readiness < pick


def test_local_inventory_is_validated_and_restricted_to_loopback():
    assert "function _normalizeLocalModelInventory" in PICKER
    assert "parsed.protocol !== 'http:'" in PICKER
    assert "parsed.hostname === '127.0.0.1'" in PICKER
    assert "/^[a-z][a-z0-9_-]{0,31}$/.test(key)" in PICKER
    assert "_LOCAL_MODEL_STATES.has(state)" in PICKER


def test_reasoning_control_listens_on_same_event_target_that_dispatches():
    assert "document.dispatchEvent(new CustomEvent('odysseus:model-picker-opened'))" in PICKER
    assert "document.addEventListener('odysseus:model-picker-opened', load)" in PICKER
    assert "menu.addEventListener('odysseus:model-picker-opened', load)" not in PICKER


def test_reasoning_control_is_hidden_until_qwen_is_selected():
    assert 'id="qwen-reasoning-row" hidden' in HTML
    assert ".model-picker-footer[hidden] { display: none; }" in CSS
    assert "row.hidden = !visible" in PICKER
    assert "_selectedModelId() === qwen.alias" in PICKER


def test_picker_exposes_installed_model_loading_state():
    assert "_addSection('On this PC')" in PICKER
    assert "Loading ${model.display} into the GPUs" in PICKER
    assert "model-switch-local-loading" in PICKER
    assert "Saved — model ready" in PICKER
