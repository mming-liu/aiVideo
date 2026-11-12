"""step02_video_gen.request_plan - 冻结"真正会发出去的请求"（计划 §V1）.

一次组装、一处冻结：``FrozenVideoRequest v1`` 记录 **provider 实际生效** 的
prompt / 参数 / 引用 role 与有序素材摘要；``request_key`` 是这些事实输入的规范
摘要，用于：
- 判断"同样的事实"（缓存命中、重复请求、no_change 停止）；
- 与 ``request_id``（这一次请求的身份）分开——key 相同不代表同一个远端任务。

**不进入 key**：上传 URL / 临时签名 / 凭据 / 本地输出路径（§V1 第 3 段）。
素材字节摘要与角色绑定**进入** key。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field

SCHEMA_FROZEN_REQUEST = "video_request.v1"

#: 允许的引用角色（预检用；provider 不支持的角色 → blocked，见 §V1 预检分支）
ROLE_FIRST_FRAME = "first_frame"
ROLE_LAST_FRAME = "last_frame"
ROLE_KEYFRAME = "keyframe"
ROLE_REFERENCE = "reference"
ROLE_DESIGN_REFERENCE = "design_reference"   # 角色肖像作"设计参考"，不冒充精确起点
KNOWN_ROLES = frozenset({ROLE_FIRST_FRAME, ROLE_LAST_FRAME, ROLE_KEYFRAME,
                         ROLE_REFERENCE, ROLE_DESIGN_REFERENCE})


def _sha256_file(path: str | None) -> str | None:
    if not path:
        return None
    from shared.enhancement_io import sha256_file
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return None
    return sha256_file(p)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


@dataclass
class ReferenceBinding:
    ref_id: str
    role: str
    path: str | None
    sha256: str | None = None
    bindings: list = field(default_factory=list)   # 实体 ID（角色/道具）
    inherit: list = field(default_factory=list)    # 从上一镜继承的语义
    exclude: list = field(default_factory=list)    # 明确排除的语义

    def key_facts(self) -> dict:
        return {"ref_id": self.ref_id, "role": self.role, "sha256": self.sha256,
                "bindings": sorted(map(str, self.bindings)),
                "inherit": sorted(map(str, self.inherit)),
                "exclude": sorted(map(str, self.exclude))}


@dataclass
class FrozenVideoRequest:
    """provider 实际生效的请求事实（组装完成后不再改动）。"""

    request_id: str
    provider: str
    model_id: str
    mode: str                                  # image | r2v | text（路由分类）
    prompt: str
    #: S03：由最终素材 role 派生的素材模式（first_last/first/reference/...）。
    #: 不进入 request_key（references 的 role 已足以区分），但随冻结记录持久化。
    media_mode: str = ""
    parameters: dict = field(default_factory=dict)     # duration/size/seed/...
    references: list[ReferenceBinding] = field(default_factory=list)
    requirements: dict = field(default_factory=dict)   # start/end state、events、audio_policy、required_checks
    source: dict = field(default_factory=dict)         # scope/shot_id/shot_index/script_revision/source_spans
    capability_revision: str | None = None
    #: 上游素材选择记录（解释性事实：**不进** request_key，但随冻结/准备/保存/恢复透传）
    media_selection: dict = field(default_factory=dict)
    request_key: str = ""
    schema_version: str = SCHEMA_FROZEN_REQUEST

    # -- 序列化（用于 manifest / 事件 / 复现） --
    def to_dict(self) -> dict:
        d = asdict(self)
        d["references"] = [asdict(r) for r in self.references]
        return d

    def key_facts(self) -> dict:
        """进入 ``request_key`` 的事实（**排除** URL/签名/凭据/本地输出路径）。"""
        return {
            "schema": self.schema_version,
            "provider": self.provider,
            "model_id": self.model_id,
            "mode": self.mode,
            "prompt": self.prompt,
            "parameters": {k: self.parameters.get(k) for k in sorted(self.parameters)},
            "references": [r.key_facts() for r in self.references],
            "requirements": self.requirements,
            "source": {"scope": self.source.get("scope"),
                       "script_revision": self.source.get("script_revision"),
                       "shot_id": self.source.get("shot_id")},
            "capability_revision": self.capability_revision,
        }

    def compute_key(self) -> str:
        from shared.enhancement_io import canonical_json_digest
        self.request_key = canonical_json_digest(self.key_facts())
        return self.request_key


def new_request_id() -> str:
    import os
    return f"req-{os.getpid()}-{time.time_ns()}"


def classify_mode(req) -> str:
    """按实际提交形态判定 mode（不是按配置标签）。"""
    if getattr(req, "reference_paths", None):
        return "r2v"
    if (getattr(req, "image_path", None) or getattr(req, "last_frame_image_path", None)
            or getattr(req, "extra_keyframe_paths", None)):
        return "image"
    return "text"


def collect_references(req) -> list[ReferenceBinding]:
    """把 VideoGenerationRequest 的四类帧/参考字段转成带 role 的有序绑定。"""
    refs: list[ReferenceBinding] = []

    def _add(role: str, path: str | None, bindings=None, inherit=None, exclude=None):
        if not path:
            return
        refs.append(ReferenceBinding(
            ref_id=f"r{len(refs) + 1}", role=role, path=str(path),
            sha256=_sha256_file(path), bindings=list(bindings or []),
            inherit=list(inherit or []), exclude=list(exclude or [])))

    _add(ROLE_FIRST_FRAME, getattr(req, "image_path", None))
    _add(ROLE_LAST_FRAME, getattr(req, "last_frame_image_path", None))
    for p in (getattr(req, "extra_keyframe_paths", None) or []):
        _add(ROLE_KEYFRAME, p)
    for p in (getattr(req, "reference_paths", None) or []):
        _add(ROLE_REFERENCE, p)
    return refs


#: provider 素材角色 → 冻结请求的**规范**角色.
#:
#: provider 的 ``content_plan`` 用它自己的词汇（``reference_image`` /
#: ``reference_audio`` / ``image``…），冻结契约只认 ``KNOWN_ROLES``。不规范化就会把
#: **合法请求**在预检里判成 ``unknown_role`` 并在 POST 前拒收（2026-10-04 复核 P1-4）。
#: 规范表只做**同义归并**：表里没有的角色原样保留，仍由预检拒收（fail-closed）。

ROLE_CANONICAL = {
    "first_frame": ROLE_FIRST_FRAME,
    "last_frame": ROLE_LAST_FRAME,
    "keyframe": ROLE_KEYFRAME,
    "key_frame": ROLE_KEYFRAME,
    "reference": ROLE_REFERENCE,
    "reference_image": ROLE_REFERENCE,
    "reference_audio": ROLE_REFERENCE,
    # T1（2026-11）：原生视频参考是 reference 的一种；冻结契约只认规范角色，
    # 不把 reference_video 当未知角色在 POST 前拒收。
    "reference_video": ROLE_REFERENCE,
    "video": ROLE_REFERENCE,
    "image": ROLE_REFERENCE,
    "audio": ROLE_REFERENCE,
}


def canonical_role(role: str) -> str:
    """把 provider 角色名归一到冻结契约的角色（表外原样返回）."""
    return ROLE_CANONICAL.get(str(role or ""), str(role or ""))


def _references_from_materials(materials: list) -> list[ReferenceBinding]:
    """把 prepared 的**有序素材**（按字节摘要与角色定身份）转成引用绑定."""
    refs: list[ReferenceBinding] = []
    for i, m in enumerate(materials):
        path = getattr(m, "source_path", None)
        if not path:
            continue
        refs.append(ReferenceBinding(
            ref_id=str(getattr(m, "ref_id", "") or f"r{i + 1}"),
            role=canonical_role(getattr(m, "role", "")),
            path=str(path), sha256=getattr(m, "sha256", None)))
    return refs


def build_frozen_request(req, *, shot: dict, scope: dict | None, script_revision: str,
                         provider: str, model_id: str, capability_revision: str | None = None,
                         requirements: dict | None = None,
                         prepared=None) -> FrozenVideoRequest:
    """冻结"真正会发出去的请求"。

    ``prepared``（provider ``prepare_request`` 的结果）给定时，**以它为准**：
    prompt / mode / 参数 / 有序素材都取它的事实——它才是实际提交的那一份。
    只按原请求冻结会记下与提交不符的值（2026-10-03 复核实测：实际提交 10 秒、
    冻结记录仍写 6 秒，`request_key` 却完全相同），缓存与恢复验收因此全部失真。

    未给 ``prepared``（旧调用点/离线规划）时退回按请求冻结，行为与原来一致。
    """
    shot_id = shot.get("shot_id") or shot.get("id") or f"shot-{shot.get('index')}"
    params = {
        "duration": getattr(req, "duration", None),
        "size": getattr(req, "size", None),
        "seed": getattr(req, "seed", None),
        "negative_prompt": getattr(req, "negative_prompt", None),
        "camera_movement": getattr(req, "camera_movement", None),
    }
    references = collect_references(req)
    prompt = str(req.prompt or "")
    mode = classify_mode(req)
    if prepared is not None:
        # prepared 是下发事实源；未下发的 seed/negative_prompt 不得补回。
        params = dict(getattr(prepared, "parameters", None) or {})
        materials = list(getattr(prepared, "materials", None) or [])
        if materials:
            references = _references_from_materials(materials)
        prompt = str(getattr(prepared, "prompt", None) or prompt)
        mode = str(getattr(prepared, "mode", None) or mode) or mode
    media_selection = dict(getattr(req, "media_selection", None) or {})
    if prepared is not None:
        _mods = list(getattr(prepared, "modifications", None) or [])
        if _mods:
            _merged = list(media_selection.get("modifications") or [])
            for _m in _mods:
                if _m not in _merged:
                    _merged.append(dict(_m))
            media_selection["modifications"] = _merged
    # S03：媒体模式优先取 prepared（实际下发）的派生值，否则按最终 references 派生。
    from shared.video_media_plan import derive_media_mode
    _media_mode = str(getattr(prepared, "media_mode", "") or "")
    if not _media_mode:
        _media_mode = derive_media_mode([r.role for r in references])
    frozen = FrozenVideoRequest(
        request_id=new_request_id(),
        provider=provider,
        model_id=model_id,
        mode=mode,
        media_mode=_media_mode,
        prompt=prompt,
        parameters=params,
        references=references,
        requirements=dict(requirements or {}),
        source={
            "scope": scope or {},
            "shot_id": str(shot_id),
            "shot_index": shot.get("index"),
            "script_revision": script_revision,
            "source_spans": shot.get("source_spans") or [],
        },
        capability_revision=capability_revision,
        media_selection=media_selection,
    )
    frozen.compute_key()
    return frozen


def _norm_fact(value) -> str:
    import json as _json
    return _json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def verify_prepared_frozen(prepared, frozen: FrozenVideoRequest) -> list[dict]:
    """prepared（实际下发）与 frozen（冻结记录）的一致性核对。

    prompt 逐字一致；其余字段按规范化结构比较（模型/参数/素材角色+顺序+摘要），
    不因 JSON 键顺序或传输编码差异误判。
    """
    problems: list[dict] = []

    def _p(code: str, field_path: str, message: str):
        problems.append({"code": code, "field_path": field_path, "message": message})

    if str(getattr(prepared, "prompt", "") or "") != str(frozen.prompt or ""):
        _p("prepared_prompt_mismatch", "prompt", "prepared 与 frozen 的 prompt 不是逐字一致")
    for field in ("provider", "model_id", "mode"):
        a = str(getattr(prepared, field, "") or "")
        b = str(getattr(frozen, field, "") or "")
        if a != b:
            _p("prepared_%s_mismatch" % field, field,
               "prepared=%r frozen=%r" % (a, b))

    p_params = dict(getattr(prepared, "parameters", None) or {})
    f_params = dict(getattr(frozen, "parameters", None) or {})
    for key in sorted(set(p_params) | set(f_params)):
        if _norm_fact(p_params.get(key)) != _norm_fact(f_params.get(key)):
            _p("prepared_parameter_mismatch", "parameters.%s" % key,
               "prepared=%r frozen=%r" % (p_params.get(key), f_params.get(key)))

    p_mats = list(getattr(prepared, "materials", None) or [])
    # 与冻结同源：只有能落成 reference 的素材才参与比较（无 path 的素材不进入引用）
    expected_refs = _references_from_materials(p_mats)
    f_refs = list(getattr(frozen, "references", None) or [])
    if len(expected_refs) != len(f_refs):
        _p("prepared_material_count_mismatch", "references",
           "prepared=%d frozen=%d" % (len(expected_refs), len(f_refs)))
    else:
        for i, (mat, ref) in enumerate(zip(expected_refs, f_refs)):
            for field in ("role", "sha256"):
                a = getattr(mat, field, None)
                b = getattr(ref, field, None)
                if _norm_fact(a) != _norm_fact(b):
                    _p("prepared_material_%s_mismatch" % field, "references[%d].%s" % (i, field),
                       "prepared=%r frozen=%r" % (a, b))
            a_path = getattr(mat, "source_path", None)
            b_path = getattr(ref, "path", None)
            if a_path and b_path and _norm_fact(a_path) != _norm_fact(b_path):
                _p("prepared_material_path_mismatch", "references[%d].path" % i,
                   "prepared=%r frozen=%r" % (a_path, b_path))

    # S03：media_mode 必须与**最终 role** 一致；两边同时标错也要检出。
    # 旧记录/手工夹具没有该字段时，用 role 派生值参与比较（而不是直接判不等）。
    from shared.video_media_plan import derive_media_mode
    p_media = str(getattr(prepared, "media_mode", "") or "")
    f_media = str(getattr(frozen, "media_mode", "") or "")
    p_derived = derive_media_mode([getattr(m, "role", "") for m in p_mats])
    f_derived = derive_media_mode([getattr(r, "role", "") for r in f_refs])
    p_eff = p_media or p_derived
    f_eff = f_media or f_derived
    if p_eff != f_eff:
        _p("prepared_media_mode_mismatch", "media_mode",
           "prepared=%r(%r) frozen=%r(%r)" % (p_media, p_eff, f_media, f_eff))
    if p_media and p_derived != p_media:
        _p("prepared_media_mode_role_mismatch", "media_mode",
           "标签=%r 但素材 role 派生=%r" % (p_media, p_derived))
    if f_media and f_derived != f_media:
        _p("frozen_media_mode_role_mismatch", "media_mode",
           "标签=%r 但引用 role 派生=%r" % (f_media, f_derived))
    return problems


def validate_frozen_request(frozen: FrozenVideoRequest, *,
                            supported_roles: set[str] | None = None,
                            max_references: int | None = None) -> list[dict]:
    """结构预检（**不**声明语义已验证）。返回 problems 列表（空 = 结构 ready）。

    problems: ``{"field_path": ..., "code": ..., "message": ..., "needed_change": ...}``
    """
    problems: list[dict] = []

    def _p(code: str, field_path: str, message: str, needed: str = ""):
        problems.append({"code": code, "field_path": field_path,
                         "message": message, "needed_change": needed})

    if not frozen.prompt.strip():
        _p("prompt_empty", "prompt", "prompt 为空，无可提交内容", "补齐 prompt 或返回生产者")
    if not frozen.model_id:
        _p("model_missing", "model_id", "未解析出实际模型 ID", "在能力注册表登记后重试")
    for r in frozen.references:
        if r.role not in KNOWN_ROLES:
            _p("unknown_role", f"references[{r.ref_id}].role", f"未知引用角色 {r.role!r}",
               "改用已登记角色或返回生产者")
        if supported_roles is not None and r.role not in supported_roles:
            _p("role_unsupported", f"references[{r.ref_id}].role",
               f"provider {frozen.provider} 不支持角色 {r.role!r}", "换支持该角色的 provider 或改参考绑定")
        if r.path and r.sha256 is None:
            _p("reference_missing", f"references[{r.ref_id}].path",
               f"引用素材不存在或不可读: {r.path}", "回到关键帧/参考图生产阶段")
    if max_references is not None and len(frozen.references) > max_references:
        _p("reference_capacity", "references",
           f"参考数量 {len(frozen.references)} 超过 provider 容量 {max_references}",
           "删除冗余参考/拆分镜头后重交（不得无提示丢弃关键人物或道具）")
    dur = frozen.parameters.get("duration")
    if dur is not None and (not isinstance(dur, (int, float)) or dur <= 0):
        _p("bad_duration", "parameters.duration", f"非法时长: {dur!r}", "由 timecode 重新计算")
    return problems
