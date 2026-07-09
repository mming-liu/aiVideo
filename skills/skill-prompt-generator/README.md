# Skill Prompt Generator - 基于Skills的智能提示词生成系统

> 🎉 **v2.0 已发布！** 新增跨domain查询和设计系统集成。[查看升级指南 →](UPGRADE_GUIDE_v2.0.md)
>
> 🆕 **Codex 适配完成！** 支持 OpenAI Codex CLI 使用全部 Skills。[查看 Codex 指南 →](.codex/codex.md)

**一个 Claude Code Skills 项目**，通过12个专业领域Skills，基于Universal Elements Library（1246元素）和社区提示词语料库（675条 source prompts）生成高质量AI图像提示词。

同时支持 **Claude Code** 和 **OpenAI Codex CLI** 两种 Agent 平台。

## 🆕 v2.0 新特性

- 🔄 **跨Domain查询** - 数据库利用率从40.3%提升到79.9%，充分利用所有领域元素
- 🎨 **设计系统集成** - 融合prompt-crafter的配色方案，支持20万+组合
- 📐 **三种生成模式** - Portrait（人像）/ Cross-Domain（跨域）/ Design（设计）
- 🔧 **变量采样系统** - 参数化元素，避免重复生成
- ✅ **100%向后兼容** - v1.0功能完全保留

**[快速开始 v2.0 →](README_v2.0.md)** | **[完整升级指南 →](UPGRADE_GUIDE_v2.0.md)**

## 🎯 项目定位

**这不是一个普通的Python工具，而是一个完整的Skills系统：**

- 🎨 **Skills优先**：用户通过调用Skills生成提示词，不直接调用Python
- 🧠 **智能路由**：自动识别领域（人像/艺术/设计/产品/视频），调用对应专家
- 📦 **12个专业Skills**：每个领域有独立的专家Skill
- 💾 **统一数据源**：所有Skills共享Universal Elements Library（1246元素）+ 社区语料库（675条）

## ✨ 核心特性

### 🎯 Skills系统（核心）
- **12个专业领域Skills**：intelligent-prompt-generator, art-master, design-master, product-master, video-master, universal-learner, prompt-analyzer, prompt-extractor, prompt-generator, prompt-master, prompt-xray, domain-classifier, prompt-writer 等
- **智能领域路由**：自动识别用户需求，调用对应专家
- **模块化架构**：每个Skill独立工作，协同配合
- **🆕 Codex 适配**：通过 `.codex/` 目录同时支持 OpenAI Codex CLI（由 @Felictycf 贡献）

### 🆕 v2.0 三种生成模式
- **Portrait（人像）** - 纯人像摄影，使用portrait domain（502元素）
- **Cross-Domain（跨域）** - 复杂场景，自动组合多个domains（995元素）
- **Design（设计）** - 海报卡片，SQLite元素 + YAML配色（20万+组合）

### 🧠 智能能力
- **语义理解**：区分主体/风格/氛围
- **常识推理**：自动推断合理属性（如人种→眼睛颜色）
- **一致性检查**：自动检测并修正逻辑冲突
- **框架驱动**：基于`prompt_framework.yaml`结构化生成
- **🆕 跨域查询**：自动识别所需domains并智能组合
- **🆕 变量采样**：参数化元素，智能避免重复

### 📦 双轨制系统
- **元素级生成**：从1246个元素中智能选择组合
- **模板级生成**：完整设计系统模板（如Apple PPT模板）
- **🆕 社区语料库**：675条社区 source prompts（260+创作者，覆盖海报/人像/UI/产品/电商/广告创意/角色设计等7大类）
- **🆕 设计变量库**：37种配色方案 + 边框 + 装饰元素

### 📦 支持领域
- 📷 **portrait** - 人像摄影（502个元素）
- 🎨 **design** - 平面设计（166个元素，含5个完整模板）
- 🏠 **interior** - 室内设计（79个元素）
- 📦 **product** - 产品摄影（78个元素）
- 🎭 **art** - 艺术风格（70个元素）
- 🎬 **video** - 视频生成（49个元素）
- 📸 **common** - 通用摄影技术（208个元素）
- 🎨 **creative** - 创意综合（37个元素）
- 🎬 **scenario** - 场景描述（34个元素）
- 🔧 **utility** - 工具型提示词（10个元素）
- 📝 **prompt_writing** - 提示词写作（9个元素）
- 🏡 **lifestyle** - 生活方式（4个元素）
- 🆕 **跨domain** - 自动组合多个领域（995个元素）
- 🆕 **设计变量** - 配色+边框+装饰（20万+组合）

## 📦 安装

### 前置要求

- **Claude Code** - 需要安装Claude Code CLI
- **Python 3.8+** - 用于运行底层引擎
- **Git** - 用于克隆项目（可选）

### 安装步骤

#### 方式1：克隆到本地（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/huangserva/skill-prompt-generator.git

# 2. 进入项目目录
cd skill-prompt-generator

# 3. 安装Python依赖
pip install -r requirements.txt
```

**重要**：克隆后，`.claude/skills/` 下的12个Skills会自动被Claude Code识别。

#### 方式2：下载ZIP

1. 访问 https://github.com/huangserva/skill-prompt-generator
2. 点击 "Code" → "Download ZIP"
3. 解压到任意目录
4. 在该目录下运行 `pip install -r requirements.txt`

### 验证安装

在Claude Code中测试：

```
# 测试人像生成skill
生成电影级的亚洲女性

# 测试设计skill
生成Bento Grid海报
```

如果Claude Code能正确调用Skills并生成提示词，说明安装成功。

---

## 🚀 快速开始

### 方式1：通过Skills使用（推荐）⭐

**这是主要使用方式** - 在Claude Code中直接调用Skills：

```
# 人像摄影（Portrait模式）
生成电影级的亚洲女性，张艺谋电影风格

# 跨domain场景（Cross-Domain模式）🆕
生成龙珠悟空打出龟派气功的提示词

# 设计海报（Design模式）🆕
生成温馨可爱风格的儿童教育海报

# 平面设计
生成Bento Grid玻璃态海报

# 艺术绘画
生成中国水墨画山水

# 产品摄影
生成奢华手表产品摄影
```

Claude Code会自动：
1. 识别领域（人像/设计/艺术/产品）
2. 识别生成模式（Portrait/Cross-Domain/Design）🆕
3. 调用对应的专家Skill
4. 返回完美的提示词

### 方式2：直接调用v2.0 Python引擎 🆕

使用新的统一接口：

```python
from core.cross_domain_generator import CrossDomainGenerator

generator = CrossDomainGenerator()

# 自动识别类型（portrait/cross_domain/design）
result = generator.generate("龙珠悟空打出龟派气功")

print(result['type'])      # cross_domain
print(result['prompt'])    # 完整提示词
print(result['domains'])   # ['portrait', 'video', 'art', 'common']

generator.close()
```

### 方式3：使用v1.0 引擎（完全兼容）

v1.0 API完全保留，无需修改：

```python
from intelligent_generator import IntelligentGenerator

gen = IntelligentGenerator()

# 生成人像提示词（v1.0方式）
prompt = gen.generate_from_intent({
    'subject': {
        'gender': 'female',
        'ethnicity': 'East_Asian',
        'age_range': 'young_adult'
    },
    'styling': {
        'makeup': 'k_beauty'
    },
    'lighting': {
        'lighting_type': 'natural'
    }
})

print(prompt)
gen.close()
```

**注意**：
- **推荐使用方式1**（Skills）- 最简单、最智能
- **方式2**（v2.0）- 适合需要跨domain和设计系统的场景
- **方式3**（v1.0）- 适合只需要人像生成的场景

## 📖 项目结构

```
.
├── .claude/                       # ⭐ Skills系统（Claude Code）
│   ├── CLAUDE.md                  # 项目规则和Skill路由指南
│   └── skills/                    # 12个专业领域Skills
│       ├── intelligent-prompt-generator/  # 人像提示词专家
│       ├── art-master/            # 艺术风格专家
│       ├── design-master/         # 平面设计专家
│       ├── product-master/        # 产品摄影专家
│       ├── video-master/          # 视频生成专家
│       ├── universal-learner/     # 学习系统
│       ├── prompt-analyzer/       # 提示词分析
│       ├── prompt-extractor/      # 元素提取
│       ├── prompt-generator/      # 通用生成器
│       ├── prompt-master/         # 主控调度
│       ├── prompt-xray/           # X-Ray分析
│       └── domain-classifier/     # 领域分类
│
├── .codex/                        # ⭐ Skills系统（OpenAI Codex CLI）🆕
│   ├── codex.md                   # Codex 入口指南
│   ├── SKILL_ROUTING_GUIDE.md     # Skill 路由规则
│   ├── learner.md                 # 学习模块
│   └── skills/                    # 12个Codex版Skills（与.claude/对应）
│       ├── intelligent-prompt-generator/
│       ├── prompt-master/         # 含 analyzer/builder/extractor/learner/optimizer/recommender
│       ├── prompt-extractor/      # 含 Python preprocessor + 测试
│       ├── prompt-xray/           # 含 xray_helper.py
│       ├── universal-learner/     # 含 domain_classifier/element_extractor/tagger/library_updater
│       ├── product-master/        # 含 builder + grid_collage 模块
│       ├── art-master/
│       ├── design-master/
│       ├── domain-classifier/
│       ├── prompt-analyzer/
│       ├── prompt-generator/
│       ├── video-master/
│       └── ...
│
├── 🆕 core/                       # v2.0 核心模块
│   ├── cross_domain_generator.py  # 统一生成接口（主入口）
│   ├── cross_domain_query.py      # 跨domain查询引擎
│   ├── variable_sampler.py        # 变量采样系统
│   ├── yaml_sampler.py            # YAML变量采样
│   ├── design_bridge.py           # SQLite+YAML融合
│   └── schema_migration_v1.sql    # 数据库扩展脚本
│
├── 🆕 variables/                  # 设计变量库（YAML）
│   ├── colors.yaml                # 37种配色方案
│   ├── borders.yaml               # 边框样式
│   └── decorations.yaml           # 装饰元素
│
├── 🆕 design-logic/               # 设计逻辑系统
│   ├── warm-cute/                 # 温馨可爱风格
│   └── modern-minimal/            # 现代简约风格
│
├── intelligent_generator.py       # Python引擎：核心生成
├── framework_loader.py            # Python引擎：框架加载
├── element_db.py                  # Python引擎：数据库操作
├── prompt_framework.yaml          # 人像框架定义
│
├── extracted_results/
│   └── elements.db                # Universal Elements Library (1246元素) + source_prompts (675条)
│
├── README_v2.0.md                 # 🆕 v2.0快速开始
├── UPGRADE_GUIDE_v2.0.md          # 🆕 v2.0升级指南
└── README.md                      # 项目文档（本文件）
```

**架构说明**：
- **用户层**：通过 Claude Code 或 Codex CLI 调用 Skills
- **Skills层**：12个专业领域专家（`.claude/skills/` + `.codex/skills/`）
- **🆕 v2.0引擎层**：core/ 模块（跨domain + 设计系统）
- **v1.0引擎层**：Python引擎支持Skills运行（完全保留）
- **数据层**：Universal Elements Library（1246元素）+ 社区语料库（675条）+ 设计变量库

## 🎨 使用示例

### 示例1：人像摄影 - Portrait模式（intelligent-prompt-generator skill）

**用户请求**：
```
生成电影级的亚洲女性，张艺谋电影风格
```

**Skill自动处理**：
- 识别：人像摄影领域，Portrait模式
- 调用：intelligent-prompt-generator skill
- 生成：电影级人像提示词，包含戏剧性光影

**输出提示词**：
```
Cinematic portrait of young East Asian woman, dramatic lighting with rim light
and chiaroscuro effect, Zhang Yimou's signature color palette with rich reds
and golds, 85mm lens, shallow depth of field, film grain texture...
```

### 示例2：跨Domain复杂场景 - Cross-Domain模式 🆕

**用户请求**：
```
生成龙珠悟空打出龟派气功的提示词
```

**Skill自动处理**：
- 识别：跨domain场景（人物+动作+特效）
- 自动组合4个domains: portrait + video + art + common
- 生成：包含人物、动作姿势、能量特效的完整提示词

**输出提示词**：
```
Son Goku from Dragon Ball, spiky black hair, orange gi martial arts uniform,
Kamehameha pose with hands at waist forming glowing blue energy sphere,
dynamic action shot, energy beam effects, blue energy glow, cinematic lighting...
```

### 示例3：设计海报 - Design模式 🆕

**用户请求**：
```
生成温馨可爱风格的儿童教育海报
```

**Skill自动处理**：
- 识别：设计海报，需要专业配色系统
- 调用：Design模式（SQLite + YAML融合）
- 生成：完整设计规范（配色+边框+装饰+技术参数）

**输出**：
```
Color scheme: 天空蓝色系, primary color 淡紫蓝 (#C7CEEA),
Decorative elements: elements, soft natural window light,
Border style: box_shadow, round corners 20px...
```

### 示例4：平面设计（design-master skill）

**用户请求**：
```
生成Apple风格PPT模板
```

**Skill自动处理**：
- 识别：平面设计领域
- 调用：design-master skill
- 查询：Apple淡蓝商务PPT模板（12个元素完整系统）

**输出**：完整模板系统，包括背景、布局、配色、字体、视觉效果

### 示例5：艺术绘画（art-master skill）

**用户请求**：
```
生成中国水墨画山水
```

**Skill自动处理**：
- 识别：艺术绘画领域（无人物）
- 调用：art-master skill
- 生成：包含笔触、留白、泼墨等技法的提示词

### 示例6：产品摄影（product-master skill）

**用户请求**：
```
生成奢华手表产品摄影
```

**Skill自动处理**：
- 识别：产品摄影领域
- 调用：product-master skill
- 生成：商业级产品摄影提示词

## 🛠️ 核心功能

### 1. 元素库系统
- **1140+个可复用元素**
- 12大领域分类
- 复用性评分（1-10）
- SQLite数据库存储

### 2. 模板系统
- 完整设计系统保存
- 包含设计理念、使用指南
- 元素结构化组织
- 支持PPT、UI、品牌VI等

### 3. 智能生成
- 框架驱动（`prompt_framework.yaml`）
- 语义匹配和推理
- 一致性检查
- 自动冲突解决

### 4. 学习系统
- 从新提示词中提取元素
- 自动领域分类
- 复用性评分
- 持续积累知识

## 📊 数据库统计

### 元素库（当前版本）
- **总元素数**: 1246
- **Portrait领域**: 502个（人像专用）
- **Common领域**: 208个（通用技术）
- **Design领域**: 166个（平面设计，含5个完整模板）
- **Interior领域**: 79个（室内设计）
- **Product领域**: 78个（产品摄影）
- **Art领域**: 70个（艺术风格）
- **Video领域**: 49个（视频生成）
- **Creative领域**: 37个（创意综合）
- **Scenario领域**: 34个（场景描述）
- **Utility领域**: 10个（工具型提示词）
- **Prompt Writing领域**: 9个（提示词写作）
- **Lifestyle领域**: 4个（生活方式）
- **跨domain可用**: 995个（组合使用）
- **设计变量**: 37种配色 + 边框 + 装饰（20万+组合）
- **完整模板**: 5个（Apple、Material Design、Fluent Design等）

### 社区语料库 🆕
- **Source Prompts 总计**: 675条
- **已学习（completed）**: 246条
- **待处理（pending + metadata_only）**: 429条
- **覆盖类别**: 海报设计(163)、人像摄影(112)、UI设计(82)、综合创意(65)、产品摄影(7) 等
- **创作者来源**: 260+ 位社区创作者

### 性能提升（v1.0 → v2.0）
- 数据库利用率：40.3% → 79.9% ⬆️ **+98.2%**
- 生成模式：1种 → 3种 ⬆️ **+200%**
- 可用组合：固定 → 20万+ ⬆️ **100倍+**

## 🔧 配置

### prompt_framework.yaml

定义人像提示词的完整框架：
- 7大类：subject, facial, styling, expression, lighting, scene, technical
- 字段到数据库的映射
- 依赖规则（如era=ancient → makeup=traditional）
- 验证规则

## 📝 开发指南

### 添加新元素

```python
from element_db import ElementDatabase

db = ElementDatabase()
db.add_element({
    'element_id': 'portrait_expressions_010',
    'domain_id': 'portrait',
    'category_id': 'expressions',
    'name': 'serene_smile',
    'chinese_name': '宁静微笑',
    'ai_prompt_template': 'serene gentle smile...',
    'keywords': '["serene", "gentle", "peaceful"]',
    'reusability_score': 8.5
})
```

### 创建新模板

```python
template = {
    'template_id': 'template_xxx',
    'name': 'Template Name',
    'chinese_name': '模板中文名',
    'category': 'ppt_design',
    'element_ids': ['elem1', 'elem2', ...],
    'element_structure': {
        'backgrounds': ['elem1'],
        'layouts': ['elem2']
    },
    'design_philosophy': '设计理念...',
    'usage_scenarios': '使用场景...'
}
```

## 🤝 贡献

欢迎提交Issue和Pull Request！

### 贡献者
- **@Felictycf** — Codex CLI 完整适配（12个 Skills 迁移 + 路由系统 + prompt-xray/py + prompt-extractor/preprocessor）

## 📄 License

MIT License

## 📚 相关文档

- **[README_v2.0.md](README_v2.0.md)** - v2.0快速开始指南
- **[UPGRADE_GUIDE_v2.0.md](UPGRADE_GUIDE_v2.0.md)** - 详细升级指南和功能说明
- **[prompt_framework.yaml](prompt_framework.yaml)** - 人像框架配置文件

## 🙏 致谢

- 基于Claude Code Skills系统
- Universal Elements Library架构
- 框架驱动生成理念
