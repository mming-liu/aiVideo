"""Replay a pinned external lightweight comparison dataset through the planning entry point.

python -m tools.replay_lightweight_voice_edits CASES.json DATASET_ROOT
The case file supplies relative body paths, SHA-1 digests and explicit offline edits.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


def checked(root, relative, digest):
    path = root / relative
    if hashlib.sha1(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"SHA-1 mismatch: {path}")
    return path


def replay(case, root, source):
    from step01_script_gen import lightweight_plan as lw, lightweight_voice_repair as vr
    from step01_script_gen.outline import _parse_outline_json
    from step01_script_gen.plan_feasibility import check_plan_before_generation, plan_voice_entries

    def body(record):
        return _parse_outline_json(checked(root, record["body"], record["sha1"]).read_text("utf-8-sig"))

    def run(answers):
        calls = []
        def generate(req):
            answer = answers[len(calls)]
            calls.append(req.json_schema["name"])
            return SimpleNamespace(success=True, text=json.dumps(answer, ensure_ascii=False))
        result = lw.generate_lightweight_outline_result(source, service=SimpleNamespace(generate=generate))
        return result, calls

    original = body(case)
    ctx = vr.prepare(original, source)
    edits = {"edits": [{"id": uid, "text": text} for uid, text in case["edits"].items()]}
    result, calls = run([original, edits])
    assert calls == ["lightweight_outline", "lightweight_voice_edits"]
    assert result["executable"], result["failure"]
    plan = result["plan"]
    check_plan_before_generation(plan, novel_text=source)
    before = ctx["rows"]
    # Compare projected, consumed voices against the explicit case decisions by kind.
    after = plan_voice_entries(plan)
    for kind in ("dialogue", "narration"):
        selected = [case["edits"].get(r["id"], r["text"]) for r in before if r["kind"] == kind]
        assert [r["text"] for r in after if r["kind"] == kind] == [s for s in selected if s]
    speakers = lw.dialogue_speakers(plan)
    for row in before:
        text = case["edits"].get(row["id"], row["text"])
        if text and row["kind"] == "dialogue":
            assert speakers[text] == row["speaker"]
    assert (ctx["seconds"], vr.total(after)) == (case["before"], case["after"])
    assert len(plan["shot_beats"]) == sum(len(e["beats"]) for e in original["events"])
    report = {"chapter": case["chapter"], "body_sha1": case["sha1"], "before_sec": ctx["seconds"],
              "after_sec": vr.total(after), "before_lines": len(before), "after_lines": len(after),
              "edited": list(case["edits"]), "generation_preflight": "passed", "real_sends": 0}
    if "rejected" in case:
        redraw = body(case["rejected"])
        failed, requests = run([original, redraw, redraw])
        assert not failed["executable"] and len(requests) == 3
        baseline, _ = lw.build_plan(original, source)
        for key in ("shot_beats", "dialogue_lines", "narration_lines", "voice_units"):
            assert failed["plan"][key] == baseline[key]
        assert failed["acceptance"]["codes"] == ["lightweight_voice_edit_rejected"]
        report["rejected_redraw_sec"] = vr.total(vr.voices(redraw))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text("utf-8"))
    source_file = checked(args.dataset, manifest["source_file"], manifest["source_sha1"])
    with TemporaryDirectory() as tmp, patch.dict("os.environ", {
        "OUTPUT_BASE": tmp, "STEP01_LOG_PATH": os.devnull,
        "STEP01_PLAN_MODE": "lightweight", "STEP01_PRESENTATION_MODE": "legacy_audio",
    }), patch("socket.socket.connect", side_effect=AssertionError("offline replay")):
        from shared.chapter_reader import extract_section
        # This reader's scene lookup only annotates the log; it can invoke a model on cache miss.
        with patch("shared.chapter_reader._get_chapter_scenes", return_value={}), patch(
            "step01_script_gen.outline.round_archive_writer", return_value=None
        ):
            results = [replay(case, args.dataset, extract_section(str(source_file), case["chapter"]))
                       for case in manifest["cases"]]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
