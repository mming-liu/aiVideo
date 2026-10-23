"""构造内容完整候选：拆 f02/f09、换 d03/n07 声音类型（离线，不发模型调用）.

基线：real_slot_f08/candidate_plan.json（两次核验的同一候选）。
- f02 拆成 3 条（父亲病逝／十六岁接手／跻身十大豪门），来源身份仍逐字来自原文；
- f09 拆成 3 条（谎称醉酒／生意急事／连夜离开）；
- d03（内心独白）与 n07（张十三台词）互换槽位；另补 d04（生意急事）；
- 原有已两次获支持的承载（f01/f03/f04/f05/f06/f07/f08）逐字保留，只改节拍号；
- 因 f09 不在 source_revision.v1/v2 的授权 targets 内，拆分后该上下文不再适用 ⇒ 移除，
  以「新计划」身份接受全部事实的完整重验。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXP = ROOT / 'output/step01_ab/20261018T135622'
BASE = EXP / 'd11_run/real_slot_f08/candidate_plan.json'
OUT = ROOT / 'd12_work/candidate_content_v15.json'

plan = json.loads(BASE.read_text(encoding='utf-8'))
plan.pop('plan_verification', None)
plan.pop('verification_rejected', None)
plan.pop('source_revision_context', None)
_orig = list(plan['required_facts'])


def _redecl(fact: str, decl: str) -> str:
    """保留主张与证据原文，只换「｜承载：…」那一段."""
    parts = fact.split('｜')
    out = [p for p in parts if not p.strip().startswith('承载：')]
    return '｜'.join(out + ['承载：' + decl])


facts = [
    _orig[0],                                                   # f01 主人
    '杨旭的父亲杨炳坤四年前病逝｜原文证据：“四年前杨家老主人杨炳坤病逝的时候，”｜承载：B2/旁白8',
    '杨旭年仅十六岁便接手父亲的家业｜原文证据：“把兴步维艰的的家当一股脑儿交到了他年仅十六岁的独生子杨旭手中。”｜承载：B3/旁白9',
    '杨旭使杨家跻身青州十大豪门｜原文证据：“如今已跻身青州十大豪门之列了。”｜承载：B4/旁白2',
    _redecl(_orig[2], 'B5/旁白3'),                              # f05 秀才
    _redecl(_orig[3], 'B7/旁白5'),                              # f06 听香买来
    _redecl(_orig[4], 'B9/画面'),                               # f07 刺客
    _redecl(_orig[5], 'B10/旁白6'),                             # f08 伴当
    _redecl(_orig[6], 'B10/画面'),                              # f09 发现已死
    '张十三掌掴听香｜原文证据：“反手一掌，一个清脆的耳光便扇在她的脸上，把听香的半边脸庞都打木了。”｜承载：B13/画面',
    '张十三威胁听香不得声张｜原文证据：“你若不想吃官司，便听我吩咐，由我作主，莫要胡乱声张。”｜承载：B12/台词3',
    '听香答应听张十三吩咐｜原文证据：“是是是，奴家……奴家听你的，都听你的。”｜承载：B13/台词1',
    '张十三对庄仆谎称杨旭醉酒｜原文证据：“公子爷吃醉了酒，险些跌落水中，所以惊得听香姑娘尖叫起来。”｜承载：B14/台词2',
    '张十三借口生意急事要赶回城｜原文证据：“我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理”｜承载：B15/台词4',
    '张十三连夜驾车离开｜原文证据：“一柱香的时间之后，云河镇杨府别庄的大门洞开，张十三驾着马车疾驰而出，迅速投入了茫茫夜色之中……”｜承载：B16/旁白10',
]

beats = [
    '节拍：黄昏荷湾，小舟隐于碧荷，杨旭吹箫，听香撑伞侍立｜本镜信息目的：交代杨旭身份、年龄｜原文依据：杨家的主人姓杨名旭，字文轩，今年刚及弱冠。｜载体：旁白1',
    '节拍：杨旭的父亲杨炳坤四年前病逝｜本镜信息目的：交代杨旭丧父｜原文依据：四年前杨家老主人杨炳坤病逝的时候，｜载体：旁白8',
    '节拍：杨旭年仅十六岁便接手父亲的家业｜本镜信息目的：交代杨旭接手家业时的年纪｜原文依据：把兴步维艰的的家当一股脑儿交到了他年仅十六岁的独生子杨旭手中。｜载体：旁白9',
    '节拍：杨家跻身青州十大豪门｜本镜信息目的：交代杨旭发家结果｜原文依据：如今已跻身青州十大豪门之列了。｜载体：旁白2',
    '节拍：杨旭考中秀才，功名在身｜本镜信息目的：交代杨旭功名｜原文依据：杨文轩杨公子又参加府学，一举考中了诸生（秀才），有了功名在身｜载体：旁白3',
    '节拍：听香貌美手巧善服侍｜本镜信息目的：交代听香模样｜原文依据：这个名唤听香的美人儿当真不错，生就一副如花似玉的俏模样。｜载体：旁白4',
    '节拍：听香是杨旭花两百贯宝钞买来的｜本镜信息目的：补足听香来历与买价｜原文依据：前几日往泰州府去时，杨公子也不会花了两百贯宝钞的高价把她买下。｜载体：旁白5',
    '节拍：杨旭袒腹仰卧，听香俯身服侍，鱼咬钩而杨旭无暇理会｜本镜信息目的：营造旖旎氛围，为刺杀做铺垫｜原文依据：杨旭解衣宽袍，袒腹仰卧｜载体：画面',
    '节拍：刺客从水中暴起跃上船头，寒光一闪刺入杨旭心口，倒跃入水消失｜本镜信息目的：完成核心刺杀事件｜原文依据：那人右手一扬，手中一道寒光一闪｜载体：画面',
    '节拍：张十三飞掠上船，确认杨旭已死，内心震惊大计受挫｜本镜信息目的：交代张十三身份及他与杨旭之死的利害关系｜原文依据：那跃上船来的人青衣小帽打扮，正是杨旭的贴身伴当张十三｜载体：旁白6',
    '节拍：张十三心中决定暂隐死讯，离开此地寻人共商对策｜本镜信息目的：交代隐瞒死讯的动机｜原文依据：我暂且隐瞒死讯，先行离开此地，寻来他们再共商对策吧。｜载体：旁白7',
    '节拍：张十三威胁听香不得声张｜本镜信息目的：展现张十三决意隐瞒死讯｜原文依据：你若不想吃官司，便听我吩咐，由我作主，莫要胡乱声张。｜载体：台词3',
    '节拍：张十三怒极反手一记耳光扇在听香脸上，打得她半边脸发木、呆立当场；他低声威胁她若不想吃官司便听他吩咐，听香六神无主，连声答应都听他的｜本镜信息目的：表现听香被吓住、听命于张十三｜原文依据：反手一掌，一个清脆的耳光便扇在她的脸上｜载体：台词1',
    '节拍：张十三对庄仆谎称公子醉酒｜本镜信息目的：掩盖杨旭死讯｜原文依据：公子爷吃醉了酒，险些跌落水中，所以惊得听香姑娘尖叫起来。｜载体：台词2',
    '节拍：张十三对庄仆借口城里生意急事，要求备车回城｜本镜信息目的：交代离庄借口｜原文依据：我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理｜载体：台词4',
    '节拍：张十三驾马车连夜出庄｜本镜信息目的：完成尸体转移，留下悬念｜原文依据：一柱香的时间之后，云河镇杨府别庄的大门洞开，张十三驾着马车疾驰而出，迅速投入了茫茫夜色之中……｜载体：旁白10',
]

dialogue = [
    '是是是，奴家……奴家听你的，都听你的。',
    '公子爷吃醉了酒，险些跌落水中',
    '你若不想吃官司，便听我吩咐，由我作主，莫要胡乱声张。',
    '我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理',
]

narration = [
    {'text': '杨家的主人姓杨名旭，字文轩，今年刚及弱冠。',
     'source': '杨家的主人姓杨名旭，字文轩，今年刚及弱冠。', 'fact_refs': [1]},
    {'text': '如今已跻身青州十大豪门之列了。',
     'source': '如今已跻身青州十大豪门之列了。', 'fact_refs': [4]},
    {'text': '杨文轩杨公子又参加府学，一举考中了诸生（秀才），有了功名在身',
     'source': '杨文轩杨公子又参加府学，一举考中了诸生（秀才），有了功名在身', 'fact_refs': [5]},
    {'text': '这个名唤听香的美人儿当真不错，生就一副如花似玉的俏模样。',
     'source': '这个名唤听香的美人儿当真不错，生就一副如花似玉的俏模样。', 'fact_refs': []},
    {'text': '前几日往泰州府去时，杨公子也不会花了两百贯宝钞的高价把她买下。',
     'source': '前几日往泰州府去时，杨公子也不会花了两百贯宝钞的高价把她买下。', 'fact_refs': [6]},
    {'text': '那跃上船来的人青衣小帽打扮，正是杨旭的贴身伴当张十三',
     'source': '那跃上船来的人青衣小帽打扮，正是杨旭的贴身伴当张十三', 'fact_refs': [8]},
    {'text': '我暂且隐瞒死讯，先行离开此地，寻来他们再共商对策吧。',
     'source': '我暂且隐瞒死讯，先行离开此地，寻来他们再共商对策吧。', 'fact_refs': []},
    {'text': '四年前杨家老主人杨炳坤病逝的时候，',
     'source': '四年前杨家老主人杨炳坤病逝的时候，', 'fact_refs': [2]},
    {'text': '把兴步维艰的的家当一股脑儿交到了他年仅十六岁的独生子杨旭手中。',
     'source': '把兴步维艰的的家当一股脑儿交到了他年仅十六岁的独生子杨旭手中。', 'fact_refs': [3]},
    {'text': '张十三驾着马车疾驰而出，迅速投入了茫茫夜色之中……',
     'source': '一柱香的时间之后，云河镇杨府别庄的大门洞开，张十三驾着马车疾驰而出，迅速投入了茫茫夜色之中……',
     'fact_refs': [15]},
]

plan['required_facts'] = facts
plan['shot_beats'] = beats
plan['dialogue_lines'] = dialogue
plan['narration_lines'] = narration
plan['time_budget'] = '旁白与台词约 45 秒，画面动作约 40 秒，总计约 85 秒。'

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf-8')

# ---- 离线核对 ----
from shared import presentation_contract as pc           # noqa: E402
from step01_script_gen import outline as o             # noqa: E402
from step01_script_gen import plan_feasibility as pf    # noqa: E402

novel = (EXP / 'd9_feasibility/input/novel.txt').read_text(encoding='utf-8')
view, bad = pc.view_from_plan(plan)
print('view problems:', [(p.get('code'), p.get('path')) for p in (bad or [])])
if view is not None:
    probs = o.plan_acceptance_problems(plan, novel) or []
    print('acceptance:', [(p.get('code'), p.get('uid'), p.get('path')) for p in probs] or '0')
    units = [(u.get('uid'), str(u.get('text') or '')) for u in view.get('dialogue_units') or []]
    units += [(u.get('uid'), str(u.get('text') or '')) for u in view.get('narration_units') or []]
    durs = sorted(((pf._estimate_duration(t), u) for u, t in units), reverse=True)
    print('voices=%d total=%.2f max=%.2f(%s)' % (
        len(units), sum(d for d, _ in durs), durs[0][0], durs[0][1]))
    print('longest 5:', [(round(d, 2), u) for d, u in durs[:5]])
    for b in view['fact_bindings']:
        print('  %s -> %s' % (b['fact_uid'], [(c.get('kind'), pc.ref_uid(c)) for c in b['carriers']]))
