import copy
import pytest
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint


@pytest.mark.parametrize('target', ['画右斜倚举杯', '斜倚举杯', '画左跪坐捧杯', '抬头微笑'])
def test_pose_is_not_an_actor(target):
    script={'shots':[{'index':1,'char_layout':[{'name':'甲','facing':'面向'+target}],
                     'ff_desc':'甲斜倚举杯，乙跪坐捧杯。'}]}
    before=copy.deepcopy(script)
    FacingToGazeConstraint().apply(ConstraintContext.from_script(script))
    assert script==before


@pytest.mark.parametrize('actor', ['林远', '阿列克谢诺维奇', 'Alex'])
def test_structural_actor_identity_and_idempotence(actor):
    script={'shots':[{'index':1,'char_layout':[{'name':'甲','facing':'面向画右的'+actor},
                                             {'name':actor,'facing':'面向画左'}],
                     'ff_desc':'甲静坐，'+actor+'在一旁。'}]}
    FacingToGazeConstraint().apply(ConstraintContext.from_script(script))
    assert '目光凝望'+actor in script['shots'][0]['ff_desc']
    before=copy.deepcopy(script)
    FacingToGazeConstraint().apply(ConstraintContext.from_script(script))
    assert script==before
