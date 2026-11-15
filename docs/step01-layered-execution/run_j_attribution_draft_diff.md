# run j 冻结现场：修订前后可读对照

> 由 `tools/replay_run_j_attribution.py --markdown-out` 生成；零模型调用、零正式产物写入。
> 字段值来自真实回放阶段：`基线(A)` / `合并后(B前的模型写入)` / `最终返回稿(D)`。

## 终局

- accepted：`False`；reason：`no_target_resolved`；resolved：`0`
- revision：`d8781a99ab9e9befa1663ffb5393488b0d3f88954c7ea603dac631e389d6809f` → `d8781a99ab9e9befa1663ffb5393488b0d3f88954c7ea603dac631e389d6809f`（相等即整轮回退到基线）

## shot_1

| 项 | 基线(A) | 合并后(B) | 最终(D) |
| --- | --- | --- | --- |
| `prompt` | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔，素白色轻袍，头戴平定四方巾，赤着双脚] 黄昏水湾全景，五六亩荷塘碧叶连天，一叶小舟（长4.5米、宽1.3米、高0.5米，平底敞口无篷）隐于荷花丛中，船头盘膝坐着杨旭，国字脸清秀，剑眉星目，黑发束冠，素白色轻袍，头戴平定四方巾，赤着双脚，手持钓杆；身旁听香一袭春衫，黑发挽慵懒美人髻，斜插步摇，撑油纸伞面朝杨旭站立；夕阳余晖似雾，烟红漫天，柳枝低垂，暖金焦褐色调，大景深，三… | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔，素白色轻袍，头戴平定四方巾，赤着双脚] 黄昏水湾全景，五六亩荷塘碧叶连天，一叶小舟（长4.5米、宽1.3米、高0.5米，平底敞口无篷）隐于荷花丛中，船头盘膝坐着杨旭，国字脸清秀，剑眉星目，黑发束冠，素白色轻袍，头戴平定四方巾，赤着双脚，手持钓杆；身旁听香一袭春衫，黑发挽慵懒美人髻，斜插步摇，撑油纸伞面朝杨旭站立；夕阳余晖似雾，烟红漫天，柳枝低垂，暖金焦褐色调，大景深，三… | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔，素白色轻袍，头戴平定四方巾，赤着双脚] 黄昏水湾全景，五六亩荷塘碧叶连天，一叶小舟（长4.5米、宽1.3米、高0.5米，平底敞口无篷）隐于荷花丛中，船头盘膝坐着杨旭，国字脸清秀，剑眉星目，黑发束冠，素白色轻袍，头戴平定四方巾，赤着双脚，手持钓杆；身旁听香一袭春衫，黑发挽慵懒美人髻，斜插步摇，撑油纸伞面朝杨旭站立；夕阳余晖似雾，烟红漫天，柳枝低垂，暖金焦褐色调，大景深，三… |
| `motion_desc` | 前2秒：固定机位，荷塘柳枝轻摆，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）静浮荷丛；2-4秒：镜头缓慢推近，杨旭吹箫姿态入画，听香撑伞侧立；4秒后：推至中景，杨旭放下箫，侧首望向画右听香 | 前2秒：固定机位，荷塘柳枝轻摆，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）静浮荷丛；2-4秒：镜头缓慢推近，杨旭吹箫姿态入画，听香撑伞侧立；4秒后：推至中景，杨旭放下箫，侧首望向画右听香 | 前2秒：固定机位，荷塘柳枝轻摆，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）静浮荷丛；2-4秒：镜头缓慢推近，杨旭吹箫姿态入画，听香撑伞侧立；4秒后：推至中景，杨旭放下箫，侧首望向画右听香 |
| `char_layout` | [{"name": "杨旭", "position": "画中偏左", "facing": "盘膝坐船头", "role": "主", "surface": "船头"}, {"name": "听香", "position": "画中偏右", "facing": "撑伞站立", "role": "次", "surface": ""}] | [{"name": "杨旭", "position": "画中偏左", "facing": "盘膝坐船头", "role": "主", "surface": "船头"}, {"name": "听香", "position": "画中偏右", "facing": "撑伞站立", "role": "次", "surface": ""}] | [{"name": "杨旭", "position": "画中偏左", "facing": "盘膝坐船头", "role": "主", "surface": "船头"}, {"name": "听香", "position": "画中偏右", "facing": "撑伞站立", "role": "次", "surface": ""}] |
| `keyframes.t_sec` | [1.0, 3.0, 4.5] | [1.0, 3.0, 4.5] | [1.0, 3.0, 4.5] |
| `frame_layouts.ff` 长度 | 2 | 2 | 2 |
| `frame_layouts.lf` 长度 | 2 | 2 | 2 |
| `frame_layouts.kfs` 长度 | 3 | 3 | 3 |
| `kfs[0][0]` | [{"name": "杨旭", "on_screen": true, "position": "画中偏左", "pose": "盘膝坐船头", "gaze_target": "画右", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["吹箫", "素白轻袍"]}, {"name": "听香", "on_sc… | [{"name": "杨旭", "on_screen": true, "position": "画中偏左", "pose": "盘膝坐船头", "gaze_target": "画右", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["吹箫", "素白轻袍"]}, {"name": "听香", "on_sc… | [{"name": "杨旭", "on_screen": true, "position": "画中偏左", "pose": "盘膝坐船头", "gaze_target": "画右", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["吹箫", "素白轻袍"]}, {"name": "听香", "on_sc… |

## shot_4

| 项 | 基线(A) | 合并后(B) | 最终(D) |
| --- | --- | --- | --- |
| `prompt` | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔，素白色轻袍，头戴平定四方巾，赤着双脚] 全景，杨旭解衣宽袍，袒腹仰卧船头，素白轻袍散开，左手钓杆垂在湖面，右手提酒壶，仰望画上方星空；听香秀发披散，伏于杨旭股间，云鬓花颜频动；平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）浮沉，荷叶轻摇，冷蓝星光与暖橙炭火交织，浅景深，对角线构图 | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔，素白色轻袍，头戴平定四方巾，赤着双脚] 全景，杨旭解衣宽袍，袒腹仰卧船头，素白轻袍散开，左手钓杆垂在湖面，右手提酒壶，仰望画上方星空；听香秀发披散，伏于杨旭股间，云鬓花颜频动；平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）浮沉，荷叶轻摇，冷蓝星光与暖橙炭火交织，浅景深，对角线构图 | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔，素白色轻袍，头戴平定四方巾，赤着双脚] 全景，杨旭解衣宽袍，袒腹仰卧船头，素白轻袍散开，左手钓杆垂在湖面，右手提酒壶，仰望画上方星空；听香秀发披散，伏于杨旭股间，云鬓花颜频动；平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）浮沉，荷叶轻摇，冷蓝星光与暖橙炭火交织，浅景深，对角线构图 |
| `motion_desc` | 前2秒：固定机位，杨旭袒腹仰卧，听香伏于股间，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）轻浮；2-4秒：杨旭仰望画上方星空，饮一口酒，小舟受力浮沉；4秒后：杨旭双腿渐渐绷直，脚趾弯起，呼吸急促，酒壶微倾 | 前2秒：固定机位，杨旭素白色轻袍袒腹仰卧船头，听香一袭春衫伏于股间，小舟轻浮；2-4秒：杨旭仰望画上方星空，举壶饮一口酒，小舟受力浮沉；4秒后：杨旭双腿渐渐绷直，脚趾弯起，呼吸急促，酒壶微倾 | 前2秒：固定机位，杨旭袒腹仰卧，听香伏于股间，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）轻浮；2-4秒：杨旭仰望画上方星空，饮一口酒，小舟受力浮沉；4秒后：杨旭双腿渐渐绷直，脚趾弯起，呼吸急促，酒壶微倾 |
| `char_layout` | [{"name": "杨旭", "position": "画中偏左", "facing": "袒腹仰卧", "role": "主", "surface": ""}, {"name": "听香", "position": "画中偏右", "facing": "伏于股间", "role": "次", "surface": ""}] | [{"name": "杨旭", "position": "画中偏左", "facing": "袒腹仰卧船头", "role": "主", "surface": "船头"}, {"name": "听香", "position": "画中偏右", "facing": "伏于股间", "role": "次", "surface": "中舱"}] | [{"name": "杨旭", "position": "画中偏左", "facing": "袒腹仰卧", "role": "主", "surface": ""}, {"name": "听香", "position": "画中偏右", "facing": "伏于股间", "role": "次", "surface": ""}] |
| `keyframes.t_sec` | [1.0, 3.0, 4.5] | [1.0, 3.0, 4.5] | [1.0, 3.0, 4.5] |
| `frame_layouts.ff` 长度 | 2 | 2 | 2 |
| `frame_layouts.lf` 长度 | 2 | 2 | 2 |
| `frame_layouts.kfs` 长度 | 3 | 3 | 3 |
| `kfs[0][0]` | [{"name": "杨旭", "on_screen": true, "position": "画中偏左", "pose": "袒腹仰卧", "gaze_target": "画上", "props": ["钓杆", "酒壶"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["素白轻袍散开", "赤足"]}, {"name": "听香",… | [{"name": "杨旭", "on_screen": true, "position": "画中偏左", "pose": "袒腹仰卧船头，左手钓杆垂向画右湖面，右手提酒壶", "gaze_target": "画上方星空", "props": ["钓杆", "酒壶"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["素白色轻袍散开",… | [{"name": "杨旭", "on_screen": true, "position": "画中偏左", "pose": "袒腹仰卧", "gaze_target": "画上", "props": ["钓杆", "酒壶"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["素白轻袍散开", "赤足"]}, {"name": "听香",… |

本目标在回放中的字段级归属（含曾由 writer 改写或随事务回退的字段）：

| 字段 | 主阶段 | 模型写入 | writer 改写 | 事务回退 | 首发 writer |
| --- | --- | --- | --- | --- | --- |
| `char_layout.0.facing` | `transaction_rollback` | True | True | True | `identity_unification` |
| `char_layout.0.surface` | `transaction_rollback` | True | False | True | `` |
| `char_layout.1.surface` | `transaction_rollback` | True | False | True | `` |
| `ff_desc` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.ff.0.gaze_target` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.ff.0.pose` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.ff.0.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.ff.1.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.ff.1.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.0.0.gaze_target` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.0.0.pose` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.0.0.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.0.1.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.0.1.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.1.0.gaze_target` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.1.0.pose` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.1.0.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.1.0.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.1.1.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.1.1.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.0.gaze_target` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.0.pose` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.0.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.0.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.0.states.2` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.1.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.kfs.2.1.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.0.gaze_target` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.0.pose` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.0.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.0.states.1` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.0.states.2` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.1.states.0` | `transaction_rollback` | True | False | True | `` |
| `frame_layouts.lf.1.states.1` | `transaction_rollback` | True | False | True | `` |
| `lf_desc` | `transaction_rollback` | True | False | True | `` |
| `motion_desc` | `transaction_rollback` | True | True | True | `v6_43_prompt_enforcement` |

## shot_5

| 项 | 基线(A) | 合并后(B) | 最终(D) |
| --- | --- | --- | --- |
| `prompt` | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔] [神秘人: 身形精瘦，动作迅捷如蛙，面目隐于夜色水光中不可辨] 中景，夜色荷塘，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）一侧水面波分浪裂，神秘人一身水光从水中窜出，右手一扬，一道寒光（寒光是独立物体，不是从角色身上长出来的）如弦月刺向画左杨旭心口；杨旭袒腹仰卧船头，素白轻袍散开，左手钓杆垂湖面，右手酒壶已脱手；冷蓝月光，水花四溅，浅景深，对角线构图 | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔] [神秘人: 身形精瘦，动作迅捷如蛙，面目隐于夜色水光中不可辨] 中景，夜色荷塘，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）一侧水面波分浪裂，神秘人一身水光从水中窜出，右手一扬，一道寒光（寒光是独立物体，不是从角色身上长出来的）如弦月刺向画左杨旭心口；杨旭袒腹仰卧船头，素白轻袍散开，左手钓杆垂湖面，右手酒壶已脱手；冷蓝月光，水花四溅，浅景深，对角线构图 | [杨旭: 国字脸，脸型清秀，剑眉星目，鼻梁挺直，黑发束冠，身姿挺拔] [神秘人: 身形精瘦，动作迅捷如蛙，面目隐于夜色水光中不可辨] 中景，夜色荷塘，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）一侧水面波分浪裂，神秘人一身水光从水中窜出，右手一扬，一道寒光（寒光是独立物体，不是从角色身上长出来的）如弦月刺向画左杨旭心口；杨旭袒腹仰卧船头，素白轻袍散开，左手钓杆垂湖面，右手酒壶已脱手；冷蓝月光，水花四溅，浅景深，对角线构图 |
| `motion_desc` | 前1.5秒：固定机位，水面波分浪裂，神秘人从水中窜出，一按船舷跃上船头；1.5-3秒：神秘人右手一扬，寒光一闪剑光掠过画左杨旭心口；3秒后：神秘人推臂一送，双腿一弹，倒纵入水，水纹剧烈震荡 | 前1.5秒：固定机位，水面波分浪裂，神秘人从水中窜出，一按船舷跃上船头；1.5-3秒：神秘人右手一扬，寒光一闪剑光掠过画左杨旭心口；3秒后：神秘人推臂一送，双腿一弹，倒纵入水，水纹剧烈震荡 | 前1.5秒：固定机位，水面波分浪裂，神秘人从水中窜出，一按船舷跃上船头；1.5-3秒：神秘人右手一扬，寒光一闪剑光掠过画左杨旭心口；3秒后：神秘人推臂一送，双腿一弹，倒纵入水，水纹剧烈震荡 |
| `char_layout` | [{"name": "杨旭", "position": "画左", "facing": "袒腹仰卧", "role": "主", "surface": "船头"}] | [{"name": "杨旭", "position": "画左", "facing": "袒腹仰卧", "role": "主", "surface": "船头"}] | [{"name": "杨旭", "position": "画左", "facing": "袒腹仰卧", "role": "主", "surface": "船头"}] |
| `keyframes.t_sec` | [0.8, 2.5, 4.0] | [0.8, 2.5, 4.0] | [0.8, 2.5, 4.0] |
| `frame_layouts.ff` 长度 | 2 | 2 | 2 |
| `frame_layouts.lf` 长度 | 2 | 2 | 2 |
| `frame_layouts.kfs` 长度 | 3 | 3 | 3 |
| `kfs[0][0]` | [{"name": "杨旭", "on_screen": true, "position": "画左", "pose": "袒腹仰卧", "gaze_target": "画上", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["素白轻袍散开", "酒壶脱手"]}, {"name": "神秘人", "on_… | [{"name": "杨旭", "on_screen": true, "position": "画左", "pose": "袒腹仰卧", "gaze_target": "画上", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["素白轻袍散开", "酒壶脱手"]}, {"name": "神秘人", "on_… | [{"name": "杨旭", "on_screen": true, "position": "画左", "pose": "袒腹仰卧", "gaze_target": "画上", "props": ["钓杆"], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "船头"}, "states": ["素白轻袍散开", "酒壶脱手"]}, {"name": "神秘人", "on_… |

## shot_6

| 项 | 基线(A) | 合并后(B) | 最终(D) |
| --- | --- | --- | --- |
| `prompt` | [张十三: 脸型瘦削，细长丹凤眼，眼神机警，黑发束髻，身形精干，青衣小帽打扮，伴当装束] 特写，张十三脸型瘦削，细长丹凤眼，黑发束髻，青衣小帽，从岸堤飞掠上船梢，俯身就着星光查看画左杨旭；杨旭仰卧船头，心口衣料暗红洇痕，双目紧闭；听香秀发披散，脸色苍白，抓住船舷；冷蓝星光，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）剧烈摇晃，浅景深，三角构图 | [张十三: 脸型瘦削，细长丹凤眼，眼神机警，黑发束髻，身形精干，青衣小帽打扮，伴当装束] 特写，张十三脸型瘦削，细长丹凤眼，黑发束髻，青衣小帽，从岸堤飞掠上船梢，俯身就着星光查看画左杨旭；杨旭仰卧船头，心口衣料暗红洇痕，双目紧闭；听香秀发披散，脸色苍白，抓住船舷；冷蓝星光，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）剧烈摇晃，浅景深，三角构图 | [张十三: 脸型瘦削，细长丹凤眼，眼神机警，黑发束髻，身形精干，青衣小帽打扮，伴当装束] 特写，张十三脸型瘦削，细长丹凤眼，黑发束髻，青衣小帽，从岸堤飞掠上船梢，俯身就着星光查看画左杨旭；杨旭仰卧船头，心口衣料暗红洇痕，双目紧闭；听香秀发披散，脸色苍白，抓住船舷；冷蓝星光，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）剧烈摇晃，浅景深，三角构图 |
| `motion_desc` | 前2秒：固定机位，张十三从岸堤飞掠上船梢，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）剧烈摇晃；2-4秒：张十三俯身就着星光查看画左杨旭，面色凝重；4秒后：张十三双手发抖，扼紧双腕，转向画右听香 | 前2秒：固定机位，张十三从岸堤飞掠上船梢，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）剧烈摇晃；2-4秒：张十三俯身就着星光查看画左杨旭，面色凝重；4秒后：张十三双手发抖，扼紧双腕，转向画右听香 | 前2秒：固定机位，张十三从岸堤飞掠上船梢，平底敞口采莲小舟（长4.5米宽1.3米，可容2-3人）剧烈摇晃；2-4秒：张十三俯身就着星光查看画左杨旭，面色凝重；4秒后：张十三双手发抖，扼紧双腕，转向画右听香 |
| `char_layout` | [{"name": "张十三", "position": "画右岸堤", "facing": "纵身欲跃", "role": "主", "surface": ""}, {"name": "听香", "position": "画中偏右", "facing": "抓船舷", "role": "次", "surface": "船舷"}, {"name": "杨旭", "position": "画左", "facing": "仰卧", "rol… | [{"name": "张十三", "position": "画右岸堤", "facing": "纵身欲跃", "role": "主", "surface": ""}, {"name": "听香", "position": "画中偏右", "facing": "抓船舷", "role": "次", "surface": "船舷"}, {"name": "杨旭", "position": "画左", "facing": "仰卧", "rol… | [{"name": "张十三", "position": "画右岸堤", "facing": "纵身欲跃", "role": "主", "surface": ""}, {"name": "听香", "position": "画中偏右", "facing": "抓船舷", "role": "次", "surface": "船舷"}, {"name": "杨旭", "position": "画左", "facing": "仰卧", "rol… |
| `keyframes.t_sec` | [1.0, 3.0, 4.5] | [1.0, 3.0, 4.5] | [1.0, 3.0, 4.5] |
| `frame_layouts.ff` 长度 | 3 | 3 | 3 |
| `frame_layouts.lf` 长度 | 3 | 3 | 3 |
| `frame_layouts.kfs` 长度 | 3 | 3 | 3 |
| `kfs[0][0]` | [{"name": "张十三", "on_screen": true, "position": "画右岸堤", "pose": "纵身欲跃", "gaze_target": "画左", "props": [], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "岸堤"}, "states": ["青衣小帽"]}, {"name": "听香", "on_screen": tru… | [{"name": "张十三", "on_screen": true, "position": "画右岸堤", "pose": "纵身欲跃", "gaze_target": "画左", "props": [], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "岸堤"}, "states": ["青衣小帽"]}, {"name": "听香", "on_screen": tru… | [{"name": "张十三", "on_screen": true, "position": "画右岸堤", "pose": "纵身欲跃", "gaze_target": "画左", "props": [], "support": {"id": "boat_1", "kind": "一叶小舟", "surface": "岸堤"}, "states": ["青衣小帽"]}, {"name": "听香", "on_screen": tru… |
