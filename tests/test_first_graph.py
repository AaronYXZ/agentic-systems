from agentic_systems.first_graph import run


def test_specific_request_takes_answer_path():
    result = run("Explain LangGraph state")

    assert result["route"] == "answer"
    assert result["audit_log"] == ["normalize", "route:answer", "answer"]
    assert "Explain LangGraph state" in result["response"]


def test_vague_request_takes_clarification_path():
    result = run("Help")

    assert result["route"] == "clarify"
    assert result["audit_log"] == ["normalize", "route:clarify", "clarify"]
    assert "desired outcome" in result["response"]


def test_request_is_normalized_before_routing():
    result = run("  explain   agent   state  ")

    assert result["normalized_request"] == "explain agent state"
    assert result["route"] == "answer"

