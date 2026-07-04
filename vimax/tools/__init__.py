# rendering abstraction
from .protocols import ImageGenerator, VideoGenerator
from .render_backend import RenderBackend


def _try_import(modname, attrs):
    """Optional import: missing deps (google-genai/scenedetect/opencv) set to None."""
    try:
        mod = __import__(modname, fromlist=attrs)
        return {a: getattr(mod, a) for a in attrs}
    except Exception:
        return {a: None for a in attrs}


# image generators
ImageGeneratorDoubaoSeedreamYunwuAPI = _try_import("vimax.tools.image_generator_doubao_seedream_yunwu_api", ["ImageGeneratorDoubaoSeedreamYunwuAPI"])["ImageGeneratorDoubaoSeedreamYunwuAPI"]
ImageGeneratorNanobananaGoogleAPI = _try_import("vimax.tools.image_generator_nanobanana_google_api", ["ImageGeneratorNanobananaGoogleAPI"])["ImageGeneratorNanobananaGoogleAPI"]
ImageGeneratorNanobananaYunwuAPI = _try_import("vimax.tools.image_generator_nanobanana_yunwu_api", ["ImageGeneratorNanobananaYunwuAPI"])["ImageGeneratorNanobananaYunwuAPI"]

# reranker for rag
RerankerBgeSiliconapi = _try_import("vimax.tools.reranker_bge_silicon_api", ["RerankerBgeSiliconapi"])["RerankerBgeSiliconapi"]

# video generators
VideoGeneratorDoubaoSeedanceYunwuAPI = _try_import("vimax.tools.video_generator_doubao_seedance_yunwu_api", ["VideoGeneratorDoubaoSeedanceYunwuAPI"])["VideoGeneratorDoubaoSeedanceYunwuAPI"]
_omni = _try_import("vimax.tools.video_generator_omni_yunwu_api", ["VideoGeneratorOmniYunwuAPI", "VideoGeneratorOminiYunwuAPI"])
VideoGeneratorOmniYunwuAPI = _omni["VideoGeneratorOmniYunwuAPI"]
VideoGeneratorOminiYunwuAPI = _omni["VideoGeneratorOminiYunwuAPI"]
VideoGeneratorOpenRouterAPI = _try_import("vimax.tools.video_generator_openrouter_api", ["VideoGeneratorOpenRouterAPI"])["VideoGeneratorOpenRouterAPI"]
VideoGeneratorVeoGoogleAPI = _try_import("vimax.tools.video_generator_veo_google_api", ["VideoGeneratorVeoGoogleAPI"])["VideoGeneratorVeoGoogleAPI"]
VideoGeneratorVeoYunwuAPI = _try_import("vimax.tools.video_generator_veo_yunwu_api", ["VideoGeneratorVeoYunwuAPI"])["VideoGeneratorVeoYunwuAPI"]


__all__ = [
    "ImageGenerator",
    "VideoGenerator",
    "RenderBackend",
    "ImageGeneratorDoubaoSeedreamYunwuAPI",
    "ImageGeneratorNanobananaGoogleAPI",
    "ImageGeneratorNanobananaYunwuAPI",
    "RerankerBgeSiliconapi",
    "VideoGeneratorDoubaoSeedanceYunwuAPI",
    "VideoGeneratorOmniYunwuAPI",
    "VideoGeneratorOminiYunwuAPI",
    "VideoGeneratorOpenRouterAPI",
    "VideoGeneratorVeoGoogleAPI",
    "VideoGeneratorVeoYunwuAPI",
]
