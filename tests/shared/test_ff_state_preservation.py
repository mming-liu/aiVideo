import pytest
from shared.style_prefix import normalize_ff_desc, detect_ff_multi_action
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint

@pytest.mark.parametrize("text",["短刃刃身仍没入甲心口","细针尚没入乙衣袖","石块已没入泥中","甲仍没入水中","乙尚未跃起","箭矢没入门板"])
def test_existing_or_object_state_is_not_rewound_to_human_preaction(text):
    chars=[{"identifier":"甲"},{"identifier":"乙"}]
    assert normalize_ff_desc(text,characters=chars)==text
    shot={"index":7,"ff_desc":text,"motion_desc":"0-2秒：拔出物件","location":"室内"}
    ctx=ConstraintContext.from_script({"shots":[shot],"characters":chars})
    FfMotionOverlapConstraint().apply(ctx)
    assert shot["ff_desc"]==text
    FfMotionOverlapConstraint().apply(ctx)
    assert shot["ff_desc"]==text

@pytest.mark.parametrize("text",["刺客破水而出","神秘人纵身跃起"])
def test_explicit_actor_action_still_uses_existing_preaction_contract(text):
    out=normalize_ff_desc(text)
    assert out!=text
    assert not detect_ff_multi_action(out)
