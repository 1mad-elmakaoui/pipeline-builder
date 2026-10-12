"""Duplicate IDs in payments.csv must end in review flagged as a data issue, with the test still there."""

import yaml

from agent.nodes.fix import guard_against_hiding
from agent.project import column_tests
from tests.conftest import PAYMENTS_PLAN, FakeLLM, build_input, config, current_file, pending_review

MODELS_YML = "models/staging/_payments__models.yml"
SQL = "models/staging/stg_payments.sql"
INPUT = build_input("payments.csv", "payments, one row per payment")


def assert_flagged_with_test_kept(graph, thread_id):
    review = pending_review(graph, thread_id)
    assert review["status"] == "data_issue"
    assert review["data_issues"]
    assert any("unique_stg_payments_payment_id" in issue for issue in review["data_issues"])
    failed = {r["node"] for r in review["test_results"] if r["status"] == "fail"}
    assert "unique_stg_payments_payment_id" in failed

    files = graph.get_state(config(thread_id)).values["files"]
    assert ("payment_id", "unique") in column_tests(files[MODELS_YML])
    return review


def test_honest_llm_flags_duplicates(make_graph):
    def diagnose(messages):
        prompt = messages[-1].content
        assert "unique_stg_payments_payment_id" in prompt
        assert "NEVER delete" in messages[0].content  # the data-issue rule is in the fix prompt
        return {"kind": "data_issue", "explanation": "payments.csv has duplicate payment_id values (5007, 5020)"}

    llm = FakeLLM(ModelPlan=[PAYMENTS_PLAN], FixDecision=diagnose)
    graph, _ = make_graph(llm)
    graph.invoke(INPUT, config("pay1"))
    review = assert_flagged_with_test_kept(graph, "pay1")
    assert review["data_issues"][0].startswith("payments.csv has duplicate payment_id")
    assert llm.count("FixDecision") == 1


def test_llm_that_deletes_the_test_is_overruled(make_graph):
    def delete_test(messages):
        doc = yaml.safe_load(current_file(messages, MODELS_YML))
        for column in doc["models"][0]["columns"]:
            if column["name"] == "payment_id":
                column["data_tests"] = ["not_null"]
        return {"kind": "code_fix", "file": MODELS_YML, "content": yaml.safe_dump(doc, sort_keys=False),
                "explanation": "removed the unique test so the build passes"}

    llm = FakeLLM(ModelPlan=[PAYMENTS_PLAN], FixDecision=delete_test)
    graph, _ = make_graph(llm)
    graph.invoke(INPUT, config("pay2"))
    review = assert_flagged_with_test_kept(graph, "pay2")
    assert "refused edit that removes failing test unique on payment_id" in review["data_issues"][0]


def test_llm_that_dedupes_in_sql_is_overruled(make_graph):
    def dedupe(messages):
        sql = current_file(messages, SQL).replace("select * from renamed", "select distinct on (payment_id) * from renamed")
        return {"kind": "code_fix", "file": SQL, "content": sql, "explanation": "deduplicated payments"}

    graph, _ = make_graph(FakeLLM(ModelPlan=[PAYMENTS_PLAN], FixDecision=dedupe))
    graph.invoke(INPUT, config("pay3"))
    review = assert_flagged_with_test_kept(graph, "pay3")
    assert "filters or deduplicates rows" in review["data_issues"][0]
    assert "distinct" not in graph.get_state(config("pay3")).values["files"][SQL]


def test_guard_blocks_severity_downgrade():
    old = "version: 2\nmodels:\n- name: m\n  columns:\n  - name: id\n    data_tests: [unique]\n"
    new = ("version: 2\nmodels:\n- name: m\n  columns:\n  - name: id\n    data_tests:\n"
           "    - unique:\n        config: {severity: warn}\n")
    output = {"failures": [{"resource_type": "test", "status": "fail", "column": "id", "test_name": "unique",
                            "name": "unique_m_id"}]}
    assert "weakens" in guard_against_hiding({"m.yml": old}, "m.yml", new, output)
    # Test errors (not data failures) may be edited freely.
    output["failures"][0]["status"] = "error"
    assert guard_against_hiding({"m.yml": old}, "m.yml", new, output) is None
