import pytest
from pathlib import Path
from types import SimpleNamespace

from src.chat_handler import ChatHandler


class _UploadHandler:
    def resolve_upload(self, *_args, **_kwargs):
        raise AssertionError("attachments must not be resolved when tool preprocessing is disabled")

    def is_image_file(self, *_args, **_kwargs):
        raise AssertionError("images must not be inspected when tool preprocessing is disabled")


class _ImageUploadHandler:
    def __init__(self, info):
        self.info = info

    def resolve_upload(self, upload_id, owner=None):
        assert upload_id == self.info["id"]
        assert owner == "user"
        return dict(self.info)

    def is_image_file(self, *_args, **_kwargs):
        return True

    def inside_base_dir(self, path):
        return Path(path).resolve().parent == Path(self.info["path"]).resolve().parent


@pytest.mark.asyncio
async def test_preprocess_can_skip_external_context_and_attachment_work(monkeypatch):
    async def _fail_transcript(*_args, **_kwargs):
        raise AssertionError("YouTube transcripts must not be fetched")

    async def _fail_comments(*_args, **_kwargs):
        raise AssertionError("YouTube comments must not be fetched")

    monkeypatch.setattr("src.chat_handler.extract_transcript_async", _fail_transcript)
    monkeypatch.setattr("src.chat_handler.fetch_youtube_comments", _fail_comments)
    monkeypatch.setattr(
        "src.chat_handler.model_supports_vision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision support must not be probed")
        ),
    )

    handler = ChatHandler(
        session_manager=None,
        memory_manager=None,
        chat_processor=None,
        research_handler=None,
        preset_manager=None,
        upload_handler=_UploadHandler(),
    )
    sess = SimpleNamespace(model="text-only", endpoint_url="", owner="user", id="session")

    enhanced, user_content, text_ctx, youtube, attachment_meta = await handler.preprocess_message(
        "Do not use tools. https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ["image-id"],
        sess,
        auto_opened_docs=[],
        allow_tool_preprocessing=False,
    )

    assert enhanced.startswith("Do not use tools.")
    assert user_content == enhanced
    assert text_ctx == enhanced
    assert youtube == []
    assert attachment_meta == []


@pytest.mark.asyncio
async def test_vision_capable_main_model_keeps_image_as_multimodal_block(
    tmp_path, monkeypatch
):
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    info = {
        "id": "image-id",
        "name": "diagram.png",
        "mime": "image/png",
        "path": str(image),
        "size": image.stat().st_size,
    }
    monkeypatch.setattr("src.chat_handler.UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("src.chat_handler.model_supports_vision", lambda *_args: True)
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: True if key == "vision_enabled" else default,
    )

    handler = ChatHandler(
        session_manager=None,
        memory_manager=None,
        chat_processor=None,
        research_handler=None,
        preset_manager=None,
        upload_handler=_ImageUploadHandler(info),
    )
    sess = SimpleNamespace(
        model="huihui-qwen3.8-27b-abliterated-q6-k-l",
        endpoint_url="http://127.0.0.1:18085/v1/chat/completions",
        owner="user",
        id="session",
    )

    enhanced, user_content, text_ctx, youtube, attachment_meta = (
        await handler.preprocess_message(
            "What is in this image?",
            ["image-id"],
            sess,
            auto_opened_docs=[],
        )
    )

    assert "[Image attached: diagram.png]" in enhanced
    assert isinstance(user_content, list)
    image_blocks = [item for item in user_content if item.get("type") == "image_url"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "[Image attached: diagram.png]" in text_ctx
    assert youtube == []
    assert attachment_meta[0]["vision_model"] == sess.model
