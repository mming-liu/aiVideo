"""production_context: VIDEO_STYLE must be locked with the adoption receipt."""


def test_video_style_is_a_locked_context_key():
    from tools import production_context as pc

    assert "VIDEO_STYLE" in pc.CONFIG_KEYS
    assert "VIDEO_STYLE" in pc.CONTEXT_ENV_KEYS

    receipt = {
        "scope": {"novel": "novel", "chapter": 1},
        "output_base_override": "out",
        "effective_config": {"VIDEO_STYLE": "anime"},
    }
    env = pc.context_env(receipt)
    assert env["VIDEO_STYLE"] == "anime"
