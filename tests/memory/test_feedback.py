"""测试 FeedbackStore + FeedbackVerifier — 反馈闭环引擎。"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from core.memory.feedback import (
    FeedbackStore, FeedbackVerifier,
    _detect_language,
)
from core.context.token_counter import estimate_tokens


# ── Helpers ────────────────────────────────────────────────────────────

class _FakeFragment:
    """模拟 ContextFragment。"""
    def __init__(self, content, score=0.5):
        self.content = content
        self.score = score
        self.tokens = 10
        self.type = "memory"
        self.pinned = False


# ── Language Detection ─────────────────────────────────────────────────

class TestDetectLanguage:
    def test_chinese_text(self):
        assert _detect_language("用中文回复") == "zh"
        assert _detect_language("这是一个中文句子") == "zh"

    def test_english_text(self):
        assert _detect_language("reply in English") == "en"
        assert _detect_language("this is an English sentence") == "en"

    def test_empty_defaults_zh(self):
        assert _detect_language("") == "zh"

    def test_mixed_prefers_zh_when_cjk_dominant(self):
        # "我喜欢 Python 编程" — mostly CJK
        assert _detect_language("我喜欢Python编程") == "zh"

    def test_mixed_prefers_en_when_ascii_dominant(self):
        # "I love coding in Python" — mostly ASCII
        assert _detect_language("I love coding in Python") == "en"


# ── Constraint Extraction ──────────────────────────────────────────────

class TestConstraintExtraction:
    def test_extract_language_zh(self):
        verifier = FeedbackVerifier(MagicMock())
        assert verifier._extract_language_constraint("用中文回复") == "zh"
        assert verifier._extract_language_constraint("请中文回答") == "zh"
        assert verifier._extract_language_constraint("写中文") == "zh"
        assert verifier._extract_language_constraint("请说中文") == "zh"

    def test_extract_language_en(self):
        verifier = FeedbackVerifier(MagicMock())
        assert verifier._extract_language_constraint("reply in English") == "en"
        assert verifier._extract_language_constraint("prefer English answers") == "en"
        assert verifier._extract_language_constraint("English only") == "en"

    def test_extract_language_none(self):
        verifier = FeedbackVerifier(MagicMock())
        assert verifier._extract_language_constraint("技术栈: Python, Rust") is None
        assert verifier._extract_language_constraint("喜欢简洁代码") is None
        assert verifier._extract_language_constraint("tech stack: Python") is None

    def test_extract_length_concise(self):
        verifier = FeedbackVerifier(MagicMock())
        assert verifier._extract_length_constraint("请简洁回复") == "concise"
        assert verifier._extract_length_constraint("be concise") == "concise"
        assert verifier._extract_length_constraint("keep it short") == "concise"
        assert verifier._extract_length_constraint("别啰嗦") == "concise"

    def test_extract_length_detailed(self):
        verifier = FeedbackVerifier(MagicMock())
        assert verifier._extract_length_constraint("请详细回复") == "detailed"
        assert verifier._extract_length_constraint("be detailed") == "detailed"
        assert verifier._extract_length_constraint("explain fully") == "detailed"

    def test_extract_length_none(self):
        verifier = FeedbackVerifier(MagicMock())
        assert verifier._extract_length_constraint("技术栈: Python") is None

    def test_irrelevant_fragment_no_constraint(self):
        verifier = FeedbackVerifier(MagicMock())
        frag = _FakeFragment("技术栈: Python, Rust")
        verifier.verify([frag], "some response")
        # Should not crash, should not record anything
        # (verify is void — no return to check, so just ensure no exception)


# ── FeedbackStore ──────────────────────────────────────────────────────

class TestFeedbackStore:
    def test_get_weight_default(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        assert store.get_weight("never seen") == 1.0

    def test_record_deviation_boosts_weight(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        w1 = store.record("用中文回复", "language", compliant=False)
        assert w1 > 1.0
        w2 = store.record("用中文回复", "language", compliant=False)
        assert w2 > w1

    def test_record_compliance_decays_weight(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        # Boost first
        store.record("用中文回复", "language", compliant=False)
        store.record("用中文回复", "language", compliant=False)
        # Then comply
        w = store.record("用中文回复", "language", compliant=True)
        assert w < 2.25  # Should be lower than the peak

    def test_weight_never_below_one(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        for _ in range(20):
            store.record("always good", "language", compliant=True)
        assert store.get_weight("always good") >= 1.0

    def test_persistence(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store1 = FeedbackStore(agent)
        store1.record("用中文回复", "language", compliant=False)
        store1.save()

        store2 = FeedbackStore(agent)
        weight = store2.get_weight("用中文回复")
        assert weight > 1.0

    def test_different_keys_independent(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        store.record("用中文回复", "language", compliant=False)
        assert store.get_weight("喜欢简洁代码") == 1.0  # Unaffected


# ── FeedbackVerifier ───────────────────────────────────────────────────

class TestFeedbackVerifier:
    def test_verify_language_compliant(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        verifier = FeedbackVerifier(store)
        frag = _FakeFragment("用中文回复")
        verifier.verify([frag], "好的，这是您要的结果。")
        assert store.get_weight("用中文回复") <= 1.0  # compliant, no boost

    def test_verify_language_deviation(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        verifier = FeedbackVerifier(store)
        frag = _FakeFragment("用中文回复")
        verifier.verify([frag], "Here is the result you requested.")
        assert store.get_weight("用中文回复") > 1.0  # deviation, boosted

    def test_verify_length_concise_compliant(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        verifier = FeedbackVerifier(store)
        frag = _FakeFragment("请简洁回复")
        verifier.verify([frag], "OK, done.")
        # Short response should be compliant with "concise"
        assert store.get_weight("请简洁回复") <= 1.0

    def test_verify_length_concise_deviation(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        verifier = FeedbackVerifier(store)
        frag = _FakeFragment("请简洁回复")
        long_response = "Very detailed. " * 200  # ~400 tokens
        verifier.verify([frag], long_response)
        assert store.get_weight("请简洁回复") > 1.0  # too long, boosted

    def test_verify_empty_response_noop(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        verifier = FeedbackVerifier(store)
        frag = _FakeFragment("用中文回复")
        verifier.verify([frag], "")  # Empty response should not crash
        assert store.get_weight("用中文回复") == 1.0  # Not recorded

    def test_verify_multiple_fragments(self, tmp_path):
        agent = tmp_path / "agent"
        agent.mkdir()
        store = FeedbackStore(agent)
        verifier = FeedbackVerifier(store)
        frags = [_FakeFragment("用中文回复"), _FakeFragment("请简洁回复")]
        verifier.verify(frags, "Here is a very detailed response in English. " * 50)
        assert store.get_weight("用中文回复") > 1.0  # language deviation
        assert store.get_weight("请简洁回复") > 1.0  # length deviation
