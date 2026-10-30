import json
from pathlib import Path
import time

import pytest

from fastapi.testclient import TestClient

from face_mapping.api import create_app
from face_mapping.lithology_judgment import LithologyJudgmentStore
from tools.generate_synthetic import generate_synthetic


def _ready(tmp_path):
    root = tmp_path / "workbench"
    photo = generate_synthetic(tmp_path / "synthetic.png", 320, 240)
    with TestClient(create_app(root)) as client:
        task = client.post("/api/tasks", files={"file": ("synthetic.png", photo.read_bytes(), "image/png")}).json()
        job = client.post(f"/api/tasks/{task['id']}/run-all", json={}).json()
        for _ in range(500):
            status = client.get(f"/api/jobs/{job['job_id']}").json()
            if status["status"] in {"completed", "failed"}:
                assert status["status"] == "completed", status
                return client, task, root
            time.sleep(.02)
        raise AssertionError("pipeline did not finish")


def test_judgment_state_is_independent_and_unconfigured_is_explicit(tmp_path):
    client, task, root = _ready(tmp_path)
    state = client.get(f"/api/tasks/{task['id']}/lithology-judgment/state")
    assert state.status_code == 200, state.text
    payload = state.json()
    assert payload["regions"]
    assert payload["segmentation"]["execution_id"]
    assert all(item.get("final_judgment") is None for item in payload["regions"])
    # The input of every region is the segmentation result, not the overlay.
    for region in payload["regions"]:
        assert region["input"]["label_map"] == "lithology_mask.png"
        assert region["input"]["pixel_source"] == "upload_original.png"
        assert region["input"]["segmentation_execution_id"] == payload["segmentation"]["execution_id"]
    result = client.post(f"/api/tasks/{task['id']}/lithology-judgment/run-all",
                         json={"model_id": "unconfigured"})
    assert result.status_code == 200
    output = result.json()
    assert output["regions"]
    for item in output["regions"]:
        assert item["final_judgment"]["status"] == "unavailable"
        assert item["final_judgment"]["conclusion_status"] == "no_candidates"
        assert "provider_not_configured" in item["final_judgment"]["reason"]
    original = json.loads((root / task["id"] / "task.json").read_text(encoding="utf-8"))
    assert original["stages"]["lithology_segmentation"]["status"] in {"completed", "warning"}


def test_knowledge_sources_and_provider_key_redaction(tmp_path):
    client, task, _ = _ready(tmp_path)
    everything = client.get("/api/knowledge/search", params={"limit": 100}).json()
    assert len(everything["site"]["records"]) == 28
    assert everything["lithology"]["status"] == "available"
    assert len(everything["lithology"]["records"]) >= 50
    assert "docx" in everything["lithology"]["source"]
    filtered = client.get("/api/knowledge/search", params={"query": "岩土类型"}).json()
    assert len(filtered["site"]["records"]) == 1
    graph = client.get("/api/knowledge/graph", params={"limit": 200}).json()
    assert graph["nodes"]
    assert any(edge["relation"] == "has_subtype" for edge in graph["edges"])
    assert any(edge["relation"] == "has_visual_profile" for edge in graph["edges"])
    saved = client.put("/api/lithology-judgment/config/default",
                       json={"base_url": "http://127.0.0.1:9/v1", "api_key": "secret-test-key", "model": "demo"})
    assert saved.status_code == 200
    serialized = json.dumps(saved.json(), ensure_ascii=False)
    assert "secret-test-key" not in serialized
    assert '"api_key":' not in serialized
    fetched = client.get("/api/lithology-judgment/config").json()
    assert fetched["providers"]["default"]["api_key_configured"] is True
    assert "secret-test-key" not in json.dumps(fetched, ensure_ascii=False)
    assert client.get(f"/api/tasks/{task['id']}/lithology-judgment/history").status_code == 200


def test_knowledge_overview_reports_built_library(tmp_path):
    client, _, _ = _ready(tmp_path)
    overview = client.get("/api/knowledge/overview").json()
    assert overview["schema_version"]
    bases = {item["key"]: item for item in overview["bases"]}
    assert bases["lithology"]["records"] >= 50
    assert bases["lithology"]["profiled"] >= 50
    assert bases["site"]["records"] == 28
    assert set(bases["lithology"]["groups"]) == {"变质岩类", "火成岩类", "沉积岩类"}
    records = client.get("/api/knowledge/records",
                         params={"kind": "lithology", "group": "火成岩类", "limit": 100}).json()
    assert records["records"]
    assert all(item["rock_group"] == "火成岩类" for item in records["records"])
    record = client.get(f"/api/knowledge/record/{records['records'][0]['record_id']}").json()
    assert record["record"]["visual_profile"]["basis"]
    assert "general_reference" in record["record"]["profile_status"]
    rebuilt = client.post("/api/knowledge/rebuild").json()
    assert rebuilt["lithology"]["records"] >= 50


def test_classify_and_retrieve_are_region_scoped(tmp_path):
    client, task, _ = _ready(tmp_path)
    regions = client.get(f"/api/tasks/{task['id']}/lithology-judgment/state").json()["regions"]
    first = regions[0]["region_id"]
    classified = client.post(f"/api/tasks/{task['id']}/lithology-judgment/classify",
                             json={"region_ids": [first], "model_id": "heuristic_color_texture_v1"})
    assert classified.status_code == 200
    by_id = {item["region_id"]: item for item in classified.json()["regions"]}
    assert by_id[first]["classification_status"] == "candidate_only"
    assert by_id[first]["candidates"]
    assert by_id[first]["visual_profile"]["source_status"] == "segmentation_result_crop"
    retrieved = client.post(f"/api/tasks/{task['id']}/lithology-judgment/retrieve",
                            json={"region_ids": [first], "query": "岩土类型"})
    assert retrieved.status_code == 200
    by_id = {item["region_id"]: item for item in retrieved.json()["regions"]}
    assert by_id[first]["retrieval_status"] == "completed"
    assert by_id[first]["knowledge_evidence"]["site"]["records"]


def test_rule_classifier_matches_profiled_rocks_not_fixed_labels(tmp_path):
    """The rule classifier must rank the catalogue by measured features."""
    client, task, root = _ready(tmp_path)
    store = LithologyJudgmentStore(client.app.state.workspace, Path(__file__).resolve().parents[1])
    granite_like = {"L": .72, "C": .03, "G": .56, "S": .28, "A": .15, "B": .06, "Z": .45}
    scored = store._kb_candidates(granite_like)
    assert scored, "knowledge base must expose profiled rocks"
    ranked = store._rank_candidates(scored, 3)
    families = store._family_candidates(scored, 3)
    assert families[0]["family"] == "花岗岩类"
    assert abs(sum(item["score"] for item in ranked) - 1.0) < .01
    assert all(item["profile_basis"] for item in ranked)
    dark_fine = {"L": .25, "C": .07, "G": .8, "S": .1, "A": .6, "B": .5, "Z": .1}
    assert store._family_candidates(store._kb_candidates(dark_fine), 3)[0]["family"] in {"片岩类", "板岩/千枚岩类"}


def test_rule_model_runs_on_every_segmentation_region(tmp_path):
    client, task, _ = _ready(tmp_path)
    result = client.post(f"/api/tasks/{task['id']}/lithology-judgment/classify",
                         json={"model_id": "rules_litho_kb_v1"})
    assert result.status_code == 200
    for region in result.json()["regions"]:
        assert region["classification_status"] in {"candidate_only", "input_unavailable", "insufficient_evidence"}
        if region["classification_status"] == "candidate_only":
            assert region["candidates"]
            assert region["families"][0]["family"]
            assert region["candidate_summary"]


def test_provider_status_is_recorded_and_redacted(tmp_path):
    client, _, _ = _ready(tmp_path)
    client.put("/api/lithology-judgment/config/default",
               json={"base_url": "http://127.0.0.1:9/v1", "api_key": "secret-test-key",
                     "model": "demo", "auth_style": "header"})
    test = client.post("/api/lithology-judgment/test-provider/default", json={}).json()
    assert test["status"] == "failed"
    assert "secret-test-key" not in json.dumps(test, ensure_ascii=False)
    stored = client.get("/api/lithology-judgment/provider-status").json()
    assert stored["providers"]["default"]["status"] == "failed"
    assert "secret-test-key" not in json.dumps(stored, ensure_ascii=False)
    config = client.get("/api/lithology-judgment/config").json()["providers"]["default"]
    assert config["auth_style"] == "header"


@pytest.mark.parametrize("style", ["bearer", "header", "api_key"])
def test_auth_styles_are_supported(style):
    headers = LithologyJudgmentStore._auth_headers({"api_key": "k", "auth_style": style})
    if style == "bearer":
        assert headers["Authorization"] == "Bearer k"
    elif style == "header":
        assert headers["api-key"] == "k"
    else:
        assert headers["Authorization"] == "Api-Key k"



def _substage(payload, sid):
    items = payload["substages"] if isinstance(payload, dict) else payload
    return next(item for item in items if item["id"] == sid)


def test_substages_run_in_order_and_invalidate_downstream(tmp_path):
    client, task, _ = _ready(tmp_path)
    base = f"/api/tasks/{task['id']}/lithology-judgment"

    initial = client.get(f"{base}/substages").json()
    assert _substage(initial, "segmentation")["status"] == "completed"
    assert _substage(initial, "algorithm_classification")["status"] == "pending"
    assert _substage(initial, "knowledge_llm")["status"] == "blocked"
    assert _substage(initial, "synthesis")["status"] == "pending"

    # The model sub-stage cannot run before algorithmic candidates exist.
    blocked = client.post(f"{base}/substages/knowledge_llm/run", json={"model_id": "rules_litho_kb_v1"})
    assert blocked.status_code == 409
    assert client.post(f"{base}/substages/unknown/run", json={}).status_code == 404

    algorithm = client.post(f"{base}/substages/algorithm_classification/run",
                            json={"model_id": "rules_litho_kb_v1"}).json()
    assert algorithm["status"] == "completed"
    payload = algorithm["state"]["substages"]
    assert _substage(payload, "algorithm_classification")["status"] == "completed"
    assert _substage(payload, "knowledge_llm")["status"] == "pending"

    usable = [item for item in algorithm["state"]["regions"] if item.get("candidates")]
    assert usable, "synthetic image should produce at least one region with candidates"
    region = usable[0]
    assert region["probability_note"]
    assert region["candidates"][0]["probability"] > 0
    assert 0 < region["candidates"][0]["probability_in_family"] <= 1
    assert abs(sum(item["probability"] for item in region["families"]) - 1) < .05

    # Run the model sub-stage, then the synthesis, so both carry source run ids.
    llm = client.post(f"{base}/substages/knowledge_llm/run", json={"model_id": "rules_litho_kb_v1"}).json()
    assert llm["status"] in {"failed", "warning", "completed"}
    synthesis = client.post(f"{base}/substages/synthesis/run", json={"model_id": "rules_litho_kb_v1"}).json()
    assert synthesis["status"] == "completed"
    for item in synthesis["state"]["regions"]:
        final = item["final_judgment"]
        assert final["conclusion_status"] in {"provisional", "no_candidates", "inconclusive", "conflict", "confirmed"}
        assert final["algorithm"]
        assert final["needs_review"] is (final["conclusion_status"] != "confirmed")

    # Re-running the algorithm invalidates both downstream sub-stages.
    rerun = client.post(f"{base}/substages/algorithm_classification/run",
                        json={"model_id": "rules_litho_kb_v1"}).json()
    payload = rerun["state"]["substages"]
    assert _substage(payload, "knowledge_llm")["status"] == "stale"
    assert _substage(payload, "synthesis")["status"] == "stale"


def test_synthesis_needs_llm_to_confirm(tmp_path):
    """Without a reachable model the conclusion stays provisional, never 'confirmed'."""
    client, task, _ = _ready(tmp_path)
    base = f"/api/tasks/{task['id']}/lithology-judgment"
    client.post(f"{base}/substages/algorithm_classification/run", json={"model_id": "rules_litho_kb_v1"})
    client.post(f"{base}/substages/knowledge_llm/run", json={"model_id": "rules_litho_kb_v1"})
    state = client.post(f"{base}/substages/synthesis/run", json={"model_id": "rules_litho_kb_v1"}).json()["state"]
    for item in state["regions"]:
        final = item["final_judgment"]
        assert final["conclusion_status"] != "confirmed"
        if item.get("candidates"):
            assert final["status"] in {"provisional", "needs_review"}
            assert "大模型" in final["reason"]


def test_switching_classifier_clears_previous_outputs(tmp_path):
    """Two models must never leave their outputs mixed in one region card."""
    client, task, _ = _ready(tmp_path)
    base = f"/api/tasks/{task['id']}/lithology-judgment"
    client.post(f"{base}/substages/algorithm_classification/run", json={"model_id": "rules_litho_kb_v1"})
    with_families = client.get(f"{base}/state").json()["regions"]
    assert any(region.get("families") for region in with_families)

    client.post(f"{base}/substages/algorithm_classification/run",
                json={"model_id": "heuristic_color_texture_v1"})
    regions = client.get(f"{base}/state").json()["regions"]
    for region in regions:
        assert not region.get("families"), region["region_id"]
        assert not region.get("probabilities")
        for candidate in region.get("candidates") or []:
            assert "lithology" not in candidate
            assert candidate.get("label")

    synthesis = client.post(f"{base}/substages/synthesis/run",
                            json={"model_id": "heuristic_color_texture_v1"}).json()
    for region in synthesis["state"]["regions"]:
        if region.get("candidates"):
            assert region["final_judgment"]["conclusion_status"] == "no_candidates"
            assert "视觉描述" in region["final_judgment"]["reason"]
