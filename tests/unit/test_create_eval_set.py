import json
import sys
from types import SimpleNamespace

import pytest

import scripts.create_eval_set as create_eval_set
from app.generation.llm import LiteLLMClient


def test_generate_requests_json_and_writes_eval_item(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "source.txt").write_text("word " * 100)
    output = tmp_path / "eval.jsonl"
    calls = []

    class FakeLLM:
        def complete(self, messages, model, response_format=None):
            calls.append((messages, model, response_format))
            return SimpleNamespace(
                text='{"question":"What is stated?","reference_answer":"The passage states it."}'
            )

    monkeypatch.setattr(create_eval_set, "LiteLLMClient", FakeLLM)
    monkeypatch.setattr(create_eval_set, "get_settings", lambda: SimpleNamespace(judge_model="test-model"))

    create_eval_set.generate(corpus, 1, output, 7, "acme")

    assert calls[0][1:] == ("test-model", {"type": "json_object"})
    item = json.loads(output.read_text().strip())
    assert item == {
        "question": "What is stated?",
        "reference_answer": "The passage states it.",
        "source_document": "acme/source",
        "validated": False,
    }


@pytest.mark.parametrize("response", ['{"question":"Q"}', "[]", '{"question":"  ","reference_answer":"A"}'])
def test_generate_skips_json_without_required_fields(tmp_path, monkeypatch, response):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "source.txt").write_text("word " * 100)

    class FakeLLM:
        def complete(self, messages, model, response_format=None):
            return SimpleNamespace(text=response)

    monkeypatch.setattr(create_eval_set, "LiteLLMClient", FakeLLM)
    monkeypatch.setattr(create_eval_set, "get_settings", lambda: SimpleNamespace(judge_model="test-model"))
    output = tmp_path / "eval.jsonl"

    create_eval_set.generate(corpus, 1, output, 7, "acme")

    assert output.read_text() == ""


def test_litellm_client_forwards_response_format(monkeypatch):
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))

    result = LiteLLMClient().complete(
        [{"role": "user", "content": "Generate JSON"}],
        "test-model",
        response_format={"type": "json_object"},
    )

    assert calls[0]["response_format"] == {"type": "json_object"}
    assert result.text == "{}"


def test_generate_retries_timeout_then_skips_file_and_continues(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "first.txt").write_text("word " * 100)
    (corpus / "second.txt").write_text("word " * 100)
    output = tmp_path / "eval.jsonl"
    calls = 0

    class FakeTimeout(Exception):
        pass

    class FakeLLM:
        def complete(self, messages, model, response_format=None):
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise FakeTimeout("simulated timeout")
            return SimpleNamespace(
                text='{"question":"Q","reference_answer":"A"}'
            )

    monkeypatch.setattr(create_eval_set, "LiteLLMTimeout", FakeTimeout)
    monkeypatch.setattr(create_eval_set, "LiteLLMClient", FakeLLM)
    monkeypatch.setattr(create_eval_set, "get_settings", lambda: SimpleNamespace(judge_model="test-model"))

    create_eval_set.generate(corpus, 1, output, 7, "acme")

    assert calls == 3
    assert len(output.read_text().splitlines()) == 1


def test_generate_saves_successful_items_before_next_llm_call(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "first.txt").write_text("word " * 100)
    (corpus / "second.txt").write_text("word " * 100)
    output = tmp_path / "eval.jsonl"
    calls = 0

    class FakeLLM:
        def complete(self, messages, model, response_format=None):
            nonlocal calls
            calls += 1
            if calls == 2:
                assert len(output.read_text().splitlines()) == 1
            return SimpleNamespace(
                text='{"question":"Q","reference_answer":"A"}'
            )

    monkeypatch.setattr(create_eval_set, "LiteLLMClient", FakeLLM)
    monkeypatch.setattr(create_eval_set, "get_settings", lambda: SimpleNamespace(judge_model="test-model"))

    create_eval_set.generate(corpus, 2, output, 7, "acme")

    assert len(output.read_text().splitlines()) == 2
