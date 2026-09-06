"""style_prefix ff 确定性修复的不变量锁定（v6.62 正则化回归防护）.

历史背景：手持物病句修复原为 5 条「左右手×物名」字面量枚举、water_break
视线改写原为 3 条字面量（含合规折叠后永不命中的「刺客高举的匕首」死条目）。
v6.62 正则化后本文件锁定：旧行为逐字不变 + 新组合正确泛化。
"""
from shared.style_prefix import _fix_ff_grammar, _preactionize_associated_clause


class TestFFGrammarHandheld:
    """手持物+身体粘连病句修复（原 _FF_GRAMMAR_HANDHELD 字面量表）."""

    def test_old_literal_entries_unchanged(self):
        """正则化前的 5 条字面量期望输出逐字保持."""
        expected = {
            "左手钓杆垂身体后仰": "左手握钓杆，钓杆垂向水面",
            "右手钓杆垂身体后仰": "右手握钓杆，钓杆垂向水面",
            "钓杆垂身体后仰": "握钓杆，钓杆垂向水面",
            "钓杆垂身": "握钓杆，钓杆垂挂",
            "鱼竿垂身体后仰": "握鱼竿，鱼竿垂向水面",
        }
        for inp, exp in expected.items():
            assert _fix_ff_grammar(inp) == exp, inp

    def test_generalizes_to_new_subject_object_pairs(self):
        """旧表没有的 主语×物名 组合也正确修复（正则化的目的）."""
        assert _fix_ff_grammar("右手鱼竿垂身") == "右手握鱼竿，鱼竿垂挂"
        assert _fix_ff_grammar("左手伞垂身体后仰") == "左手握伞，伞垂向水面"
        assert _fix_ff_grammar("扇垂身") == "握扇，扇垂挂"

    def test_long_pattern_wins_over_short(self):
        """垂身体后仰（长模式）先于 垂身（短模式）应用，不产出半截病句."""
        out = _fix_ff_grammar("左手钓杆垂身体后仰")
        assert "垂挂体" not in out
        assert out == "左手握钓杆，钓杆垂向水面"

    def test_idempotent(self):
        once = _fix_ff_grammar("左手钓杆垂身体后仰，钓杆垂身")
        twice = _fix_ff_grammar(once)
        assert once == twice

    def test_untouched_text_passthrough(self):
        assert _fix_ff_grammar("") == ""
        assert _fix_ff_grammar("他握钓杆立于船头") == "他握钓杆立于船头"


class TestWaterBreakGazeRewrite:
    """water_break 视线落点改写（原 3 条字面量 replace 链）."""

    def test_gaze_high_weapon_to_ripple(self):
        assert _preactionize_associated_clause(
            "视线汇聚于神秘人高举的匕首", "water_break"
        ) == "视线汇聚于神秘人水面涟漪"
        assert _preactionize_associated_clause(
            "视线汇聚高举的弯刀", "water_break"
        ) == "视线汇聚水面涟漪"

    def test_covers_weapon_variants_old_table_missed(self):
        """旧字面量只覆盖 匕首/弯刀，正则化后与未出水检测同一武器词表."""
        assert _preactionize_associated_clause(
            "视线紧随高举的长刀", "water_break"
        ) == "视线紧随水面涟漪"
        assert _preactionize_associated_clause(
            "视线望向高举的短刃", "water_break"
        ) == "视线望向水面涟漪"

    def test_no_gaze_keyword_untouched(self):
        """无视线词不触发改写（分支条件保持）."""
        assert _preactionize_associated_clause(
            "匕首高举", "water_break"
        ) == "匕首高举"

    def test_weapon_hidden_detection_unchanged(self):
        """未出水检测路径不受 group(1)→group(0) 调整影响."""
        assert _preactionize_associated_clause(
            "他紧握匕首蓄力", "water_break"
        ) == "匕首未出水"
