"""Provider presets: the per-vendor deviations the adapter has to absorb."""

from __future__ import annotations

import pytest

from backend.config import Settings
from backend.triage.factory import build_provider
from backend.triage.presets import PRESETS


def _provider(**overrides):
    return build_provider(Settings(_env_file=None, **overrides))


def test_stub_needs_no_key() -> None:
    assert _provider(llm_provider="stub").name == "stub"


@pytest.mark.parametrize("provider", sorted(PRESETS))
def test_every_preset_fails_loudly_without_its_key(provider: str) -> None:
    with pytest.raises(ValueError, match=PRESETS[provider].key_env):
        _provider(llm_provider=provider)


def test_openai_preset_uses_server_enforced_schema() -> None:
    provider = _provider(llm_provider="openai", openai_api_key="sk-x")
    assert provider.name == "openai"
    assert provider._structured_output == "json_schema"
    assert provider._reasoning_effort is None


def test_deepseek_preset_works_around_both_of_its_quirks() -> None:
    provider = _provider(llm_provider="deepseek", deepseek_api_key="sk-x")
    assert provider.name == "deepseek"
    # It rejects json_schema outright...
    assert provider._structured_output == "json_object"
    # ...and it is a reasoning model, which we do not want for classification.
    assert provider._reasoning_effort == "none"
    assert provider._model == "deepseek-flash"
    assert "api.deepseek.com" in provider._url


def test_explicit_settings_override_the_preset() -> None:
    provider = _provider(
        llm_provider="deepseek",
        deepseek_api_key="sk-x",
        llm_model="deepseek-v4-pro",
        llm_base_url="http://localhost:8900/v1",
    )
    assert provider._model == "deepseek-v4-pro"
    assert provider._url.startswith("http://localhost:8900/v1")


def test_generic_llm_api_key_works_for_any_provider() -> None:
    assert _provider(llm_provider="deepseek", llm_api_key="sk-generic").name == "deepseek"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown llm_provider"):
        build_provider(Settings(_env_file=None).model_copy(update={"llm_provider": "nope"}))


# ---- video source resolution -----------------------------------------------


def test_numeric_video_source_resolves_to_an_int_webcam_index() -> None:
    """cv2.VideoCapture treats a string as a filename and an int as a device
    index; it does not coerce '0' into device 0, so config must do it."""
    settings = Settings(_env_file=None, video_source="0")
    assert settings.video_source_resolved == 0
    assert isinstance(settings.video_source_resolved, int)


def test_path_video_source_stays_a_string() -> None:
    settings = Settings(_env_file=None, video_source="assets/demo_camera.mp4")
    assert settings.video_source_resolved == "assets/demo_camera.mp4"


def test_whitespace_around_a_numeric_source_is_tolerated() -> None:
    settings = Settings(_env_file=None, video_source="  2  ")
    assert settings.video_source_resolved == 2
