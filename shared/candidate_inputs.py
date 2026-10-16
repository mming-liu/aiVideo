"""shared.candidate_inputs - 跨阶段候选输入的**唯一**解析接口（计划 §F4）.

读者不按镜号猜路径、也不接受"任意视频文件"绕过准入：唯一入口是
``resolve_candidate_inputs(...)``，它消费**显式 manifest + 摘要**，按稳定
``unit_id`` 返回绝对路径 / sha256 / 媒体时窗 / 声音身份，并强制核对
scope / script_revision / shot 映射关系。

稳定 ID 规则（§F4 第三段）：
- manifest 里带 ``unit_id`` 的记录直接采用；
- 旧稿没有稳定 ID 时，**第一次冻结**为 ``scope + source_revision + 原字段 locator``
  派生的 ID，并把映射写进实验根的 ``contracts/unit_id_map.json``；
- 后续合法重编号必须提供**旧→新 map**（``id_map`` 参数），不许重新按数组位置猜身份。

视频侧候选 manifest 的单元记录形状（生产者：V 包的候选采纳出口）::

    {"unit_id": "shot-3", "shot_index": 3, "shot_id": "shot-3",
     "path": "…/outputs/shot_3.mp4", "sha256": "…",
     "window": {"start_sec": 0.0, "end_sec": 6.0},
     "media": {"duration_sec": 6.0, "width": 720, "height": 1280, "fps": 24}}

声音侧候选 manifest 的单元记录形状（生产者：T 包的 tts_candidate/采纳出口）::

    {"unit_id": "shot-3", "kind": "dialogue|narration",
     "path": "…/audio/shot_3.wav", "sha256": "…",
     "text_sha256": "…", "voice_identity": {…audio_identity.v2 侧车同形…},
     "window": {"start_sec": 0.0, "end_sec": 2.4}}
"""
from __future__ import annotations

import json
from pathlib import Path

from shared import enhancement_io as eio


#: **按包固定**的消费侧必需检查：名字取自各包生产者自己的 ``checks`` 表。
#: 不跨包套用同一个名字——视频的 ``media`` 在 TTS 的 manifest 里根本不存在，
#: 套过去只会把一切候选都判成未通过。
REQUIRED_CHECKS_BY_PACKAGE = {
    "video": ("media",),
    "tts": ("segments_lossless", "identity_complete", "units"),
}


def required_checks_for(package: str) -> tuple:
    """该包消费时的必需检查（未登记的包返回空表：**不假装**它有检查）."""
    return tuple(REQUIRED_CHECKS_BY_PACKAGE.get(str(package or ""), ()))


class CandidateInputError(eio.EnhancementError):
    def __init__(self, code: str, message: str, **fields):
        super().__init__(code, "candidate_inputs", message,
                         exit_code=eio.EXIT_REJECTED, **fields)


def _load_manifest(path: Path | str, *, expect_package: str | None = None) -> dict:
    p = Path(path)
    if not p.is_file():
        raise CandidateInputError("manifest_missing", f"候选 manifest 不存在: {p}",
                                  field_path="manifest_path")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CandidateInputError("manifest_unreadable", f"候选 manifest 读不动: {exc}",
                                  field_path="manifest_path") from exc
    if data.get("schema_version") != eio.SCHEMA_ATTEMPT_MANIFEST:
        raise CandidateInputError(
            "manifest_schema", f"候选 manifest schema 不符: {data.get('schema_version')!r}",
            field_path="schema_version")
    if expect_package and data.get("package") != expect_package:
        raise CandidateInputError(
            "manifest_package", f"候选 manifest 包名不符: {data.get('package')!r} != {expect_package!r}",
            field_path="package")
    return data


def _verify_manifest_digest(path: Path, provided_sha: str | None) -> str:
    actual = eio.sha256_file(Path(path))
    if provided_sha and provided_sha != actual:
        raise CandidateInputError("manifest_digest_mismatch",
                                  f"manifest 摘要与调用方提供的不符: {actual[:12]} != {provided_sha[:12]}",
                                  field_path="manifest_sha256")
    return actual


def _identity_of(manifest: dict) -> tuple[dict, str, str]:
    scope = manifest.get("scope") or {}
    rev = str(manifest.get("script_revision") or "")
    source = str(manifest.get("source") or "")
    return scope, rev, source


def _unit_id_map_path(context, experiment_root: Path) -> Path:
    log_dir = getattr(context, "log_dir", None) or getattr(context, "attempt_dir", None)
    base = Path(experiment_root) / "contracts"
    if log_dir is not None:
        base = Path(log_dir).parent / "contracts"
    return base / "unit_id_map.json"


def _load_id_map(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        raise CandidateInputError("unit_id_map_unreadable", f"unit_id 映射读不动: {path}")


def _stable_unit_id(scope: dict, revision: str, locator: str) -> str:
    digest = eio.canonical_json_digest({"scope": scope, "revision": revision, "locator": locator})[:16]
    return f"unit-{digest}"


def _accepted_roots(manifest_path: Path) -> list[Path]:
    """accepted 指针可能落的位置：manifest 同目录（V：一 task 一 attempt）
    或包 root（T：包级一个指针）."""
    out = [manifest_path.parent]
    if manifest_path.parent.parent.name == eio.ATTEMPTS_DIRNAME:
        out.append(manifest_path.parent.parent.parent)
    return out


def require_accepted_manifest(manifest_path: Path, manifest: dict, *,
                              required_checks: tuple = ()) -> dict:
    """准入：这份 manifest 必须被**指名它的** accepted 指针覆盖，且整条链核得过。

    "有名无实"或"有实无名"都不行：没有指针 = 这一份从未被采纳（rejected /
    unverified / 半成品都长这样），消费方一律不许用（2026-10-03 复核 P1-2）。
    """
    target = Path(manifest_path).resolve()
    tried = []
    for root in _accepted_roots(Path(manifest_path)):
        pointer = eio.read_accepted(root)
        if pointer is None:
            tried.append(f"{root}: 无指针")
            continue
        named = Path(str(pointer.get("manifest_path") or ""))
        if not named.is_absolute():
            named = (root / named)
        if named.resolve() != target:
            tried.append(f"{root}: 指针指向 {named}")
            continue
        recorded = manifest.get("request_key") or manifest.get("input_key")
        try:
            return eio.verify_accepted(
                root, required_checks=tuple(required_checks),
                expect_input_key=str(recorded) if recorded else None)
        except eio.EnhancementError as exc:
            raise CandidateInputError(
                "candidate_accepted_invalid",
                f"accepted 关系核验失败（{exc.code}）: {exc.message}",
                field_path="accepted", root=str(root)) from exc
    raise CandidateInputError(
        "candidate_not_accepted",
        "该 manifest 没有被任何 accepted 指针指名（未采纳的候选不得消费）：" + "; ".join(tried),
        field_path="accepted")


def resolve_candidate_inputs(video_manifest, audio_manifest=None, *, source: str | None = None,
                             context=None, experiment_root: Path | str | None = None,
                             expect_scope: dict | None = None,
                             expect_script_revision: str | None = None,
                             video_manifest_sha256: str | None = None,
                             audio_manifest_sha256: str | None = None,
                             id_map: dict | None = None,
                             require_audio: bool = False,
                             require_accepted: bool = True,
                             required_checks: tuple = ()) -> dict:
    """跨包候选解析：manifest → 按 unit_id 映射的输入事实。

    失败一律拒收（带 code/field_path）；**不**以任意视频文件绕过准入，也不按
    数组位置猜身份。

    准入（默认全开，``require_accepted=False`` 只给生产者自查用）：
    ① 源 manifest 必须被指名它的 **accepted 指针**覆盖，指针→manifest→产物逐条核；
    ② 消费方必须声明**期望身份**（``expect_scope`` / ``expect_script_revision``）——
       不知道期望哪一章/哪一稿就无法判"是不是这一份"，不给默认值兜底；
    ③ ``required_checks`` 声明的检查项必须通过（如媒体/QC），空表不代表通过。
    """
    v_path = Path(video_manifest)
    # 先"读得动、包名对"，再谈身份：缺文件就该报缺文件，不该被身份门挡在前面
    v = _load_manifest(v_path, expect_package="video")
    if require_accepted and expect_scope is None:
        raise CandidateInputError(
            "expect_scope_required",
            "消费候选必须声明期望的 scope（章/范围）：否则无法判断这份候选属于哪一章",
            field_path="expect_scope")
    if require_accepted:
        require_accepted_manifest(v_path, v,
                                  required_checks=required_checks_for("video") + tuple(required_checks))
    v_sha = _verify_manifest_digest(v_path, video_manifest_sha256)
    v_scope, v_rev, v_source = _identity_of(v)
    src = source or v_source

    from shared.run_scope import scope_identity

    if expect_scope is not None and scope_identity(expect_scope) != scope_identity(v_scope):
        raise CandidateInputError("scope_mismatch",
                                  f"候选 video 的 scope 与期望不符: {v_scope} != {expect_scope}",
                                  field_path="scope")
    if expect_script_revision is not None and str(expect_script_revision) != v_rev:
        raise CandidateInputError("script_revision_mismatch",
                                  f"候选 video 的 script_revision 与期望不符: {v_rev} != {expect_script_revision}",
                                  field_path="script_revision")

    root = Path(experiment_root) if experiment_root else _infer_experiment_root(v_path)
    map_path = _unit_id_map_path(context, root)
    known_map = _load_id_map(map_path)
    provided_map = dict(id_map or {})

    a: dict | None = None
    a_sha = None
    a_scope = {}
    a_rev = ""
    a_source = ""
    if audio_manifest is not None:
        a_path = Path(audio_manifest)
        a = _load_manifest(a_path, expect_package="tts")
        if require_accepted:
            require_accepted_manifest(a_path, a,
                                      required_checks=required_checks_for("tts")
                                      + tuple(required_checks))
        a_sha = _verify_manifest_digest(a_path, audio_manifest_sha256)
        a_scope, a_rev, a_source = _identity_of(a)
        if a_scope != v_scope or a_rev != v_rev:
            raise CandidateInputError(
                "video_audio_identity_mismatch",
                f"video/audio 候选的来源身份不一致（scope/revision）: {v_scope}/{v_rev} vs {a_scope}/{a_rev}",
                field_path="scope")
    elif require_audio:
        raise CandidateInputError("audio_manifest_required", "本次解析要求声音候选 manifest",
                                  field_path="audio_manifest")

    def _units(manifest: dict) -> list[dict]:
        units = manifest.get("units")
        if units is None:
            units = manifest.get("artifacts") or []
        return [u for u in units if isinstance(u, dict)]

    def _locator_of(u: dict) -> str | None:
        locator = u.get("locator") or u.get("shot_id")
        if locator is None and u.get("shot_index") is not None:
            locator = f"shots[{int(u['shot_index'])}]"
        return str(locator) if locator is not None else None

    def _resolve_uid(u: dict) -> str:
        """稳定 unit_id：显式 ID 权威，但**改绑必须给旧→新映射**；无 ID 则按 locator 冻结派生。"""
        locator = _locator_of(u)
        uid = u.get("unit_id")
        if uid:
            uid = str(uid)
            if locator is not None:
                frozen = known_map.get(locator)
                if frozen and frozen != uid:
                    if provided_map.get(str(frozen)) != uid:
                        raise CandidateInputError(
                            "unit_id_rebound_without_map",
                            f"locator {locator!r} 的冻结 unit_id={frozen} 与本条显式 {uid} 不符，"
                            "且未提供旧→新映射，拒绝静默改绑身份",
                            field_path="unit_id")
                    known_map[locator] = uid
                else:
                    known_map.setdefault(locator, uid)
            return uid
        if locator is None:
            raise CandidateInputError("unit_locator_missing",
                                      "候选记录既无 unit_id 也无 locator/shot_index，拒绝按数组位置猜身份",
                                      field_path="unit_id")
        new_id = _stable_unit_id(v_scope, v_rev, locator)
        frozen = known_map.get(locator)
        if frozen and frozen != new_id:
            # 旧稿已有冻结 ID：只有提供显式 旧→新 map 才允许改名
            replacement = provided_map.get(str(frozen))
            if not replacement:
                raise CandidateInputError(
                    "unit_id_rebound_without_map",
                    f"locator {locator!r} 的冻结 unit_id={frozen} 与当前派生 {new_id} 不符，"
                    "且未提供旧→新映射，拒绝重新按位置猜身份",
                    field_path="unit_id")
            new_id = str(replacement)
        known_map.setdefault(locator, new_id)
        return new_id

    units: dict[str, dict] = {}
    for u in _units(v):
        uid = _resolve_uid(u)
        p = Path(str(u.get("path") or ""))
        if not p.is_absolute():
            raise CandidateInputError("candidate_path_relative",
                                      f"候选路径必须绝对: {p}", field_path="path")
        if not p.is_file():
            raise CandidateInputError("candidate_missing", f"候选产物缺失: {p}", field_path="path")
        sha = str(u.get("sha256") or "")
        if sha and eio.sha256_file(p) != sha:
            raise CandidateInputError("candidate_digest_mismatch",
                                      f"候选产物摘要不符（可能被改写）: {p}", field_path="sha256")
        units[uid] = {
            "unit_id": uid, "kind": "video",
            "shot_index": u.get("shot_index"), "shot_id": u.get("shot_id"),
            "video_path": str(p.resolve()), "video_sha256": sha or eio.sha256_file(p),
            "window": u.get("window") or {}, "media": u.get("media") or {},
            "source": src, "scope": v_scope, "script_revision": v_rev,
        }

    if a is not None:
        for u in _units(a):
            uid = _resolve_uid(u)
            p = Path(str(u.get("path") or ""))
            if not p.is_absolute() or not p.is_file():
                raise CandidateInputError("candidate_missing", f"声音候选缺失或非绝对路径: {p}",
                                          field_path="path")
            sha = str(u.get("sha256") or "")
            if sha and eio.sha256_file(p) != sha:
                raise CandidateInputError("candidate_digest_mismatch",
                                          f"声音候选摘要不符: {p}", field_path="sha256")
            rec = units.setdefault(uid, {"unit_id": uid, "scope": v_scope,
                                         "script_revision": v_rev, "source": src})
            rec.update({
                "kind": u.get("kind") or rec.get("kind") or "dialogue",
                "audio_path": str(p.resolve()), "audio_sha256": sha or eio.sha256_file(p),
                "text_sha256": u.get("text_sha256"),
                "voice_identity": u.get("voice_identity") or {},
                "audio_window": u.get("window") or {},
            })

    # 冻结映射（幂等：内容不变不重写）
    if known_map:
        map_path.parent.mkdir(parents=True, exist_ok=True)
        if _load_id_map(map_path) != known_map:
            eio.commit_json_swap(map_path, eio.prepare_json_swap(map_path, known_map,
                                                                 ensure_ascii=False, indent=1))

    return {
        "schema_version": "candidate_inputs.v1",
        "source": src,
        "scope": v_scope,
        "script_revision": v_rev,
        "video_manifest_path": str(v_path.resolve()),
        "video_manifest_sha256": v_sha,
        "audio_manifest_path": str(Path(audio_manifest).resolve()) if audio_manifest else None,
        "audio_manifest_sha256": a_sha,
        "audio_source": a_source or None,
        "units": units,
    }


def _infer_experiment_root(manifest_path: Path) -> Path:
    """从 ``…/<experiment>/<package>/attempts/<id>/manifest.json`` 反推实验根。"""
    p = Path(manifest_path).resolve()
    for parent in p.parents:
        if parent.name == eio.ATTEMPTS_DIRNAME and parent.parent.parent.name == "_enhancement":
            return parent.parent.parent
        if parent.name == eio.ATTEMPTS_DIRNAME and parent.parent.parent.parent.name == "_enhancement":
            return parent.parent.parent.parent
    return p.parent
