import json
import pytest
from _text_gen import TextGenerationResult
from tools.probe_isolation import isolated_probe

@pytest.mark.parametrize("limit", [16000, 8000, 0, True])
def test_scene_analysis_actual_request_uses_config_or_rejects_before_service(monkeypatch,tmp_path,limit):
    from _text_gen.config import config
    calls=[]; factories=[]
    class Service:
        def generate(self,request):
            calls.append(request)
            return TextGenerationResult(success=True,text=json.dumps({"main_scenes":["river"],"main_characters":[],"filtered_sections":[]}))
    monkeypatch.setattr(config,"TEXT_SCENE_ANALYSIS_MAX_TOKENS",limit)
    def factory(): factories.append(True); return Service()
    monkeypatch.setattr("_text_gen.create_service",factory)
    with isolated_probe(root=tmp_path,label="scene-limit",block_transport=True):
        from shared.chapter_reader import _analyze_chapter_scenes
        result=_analyze_chapter_scenes("unused.txt",1,"novel")
    if type(limit) is int and limit>0:
        assert len(calls)==1 and calls[0].max_tokens==limit
        assert result["main_scenes"]==["river"]
    else:
        assert factories==[] and calls==[] and result["filtered_sections"]==[]
