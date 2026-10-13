"""The journey's next-step derivation names the right human step for each state and never invents a transition."""
from __future__ import annotations

from campus_api.web.steps import TASK_DEFAULTS, next_step, task_need_for


def _ns(**kw) -> dict:
    base = {"stopped": False, "mandate_state": "approved", "projects": 1, "tasks": [{"task_id": "t"}], "runs": [], "reviews": [], "artifacts": [], "workflows": []}
    base.update(kw)
    return next_step(**base)


def test_steps_follow_the_control_path() -> None:
    assert _ns(stopped=True)["code"] == "stopped"
    assert _ns(mandate_state="draft")["code"] == "decide_mandate" and _ns(mandate_state=None)["action"] == "decide"
    assert _ns(mandate_state="rejected")["action"] is None
    assert _ns(projects=0)["code"] == "create_project"
    assert _ns(tasks=[])["code"] == "create_task"
    assert _ns()["code"] == "start_workflow" and _ns()["action"] == "workflow"
    assert _ns(workflows=[{"status": "RUNNING"}])["code"] == "running"
    assert _ns(runs=[{"state": "failed"}])["code"] == "run_failed"
    assert _ns(runs=[{"state": "finished"}])["code"] == "review_open" and _ns(runs=[{"state": "finished"}])["action"] is None
    assert _ns(runs=[{"state": "finished"}], reviews=[{"id": "r"}])["code"] == "freeze_dossier"
    arts = [{"kind": "dossier", "approval": None}]
    assert _ns(runs=[{"state": "finished"}], reviews=[{"id": "r"}], artifacts=arts)["code"] == "judge"
    ok = [{"kind": "dossier", "approval": {"option": "accept", "valid_for_current": True, "basis_withdrawn_after_approval": False}}]
    assert _ns(runs=[{"state": "finished"}], reviews=[{"id": "r"}], artifacts=ok)["code"] == "approved"
    stale = [{"kind": "dossier", "approval": {"option": "accept", "valid_for_current": False, "basis_withdrawn_after_approval": False}}]
    assert _ns(runs=[{"state": "finished"}], reviews=[{"id": "r"}], artifacts=stale)["code"] == "judge", "an old approval does not cover a new version"
    rework = [{"kind": "dossier", "approval": {"option": "revise", "valid_for_current": True, "basis_withdrawn_after_approval": False}}]
    step = _ns(runs=[{"state": "finished"}], reviews=[{"id": "r"}], artifacts=rework)
    assert step["code"] == "rework" and step["action"] == "workflow", "a version returned for rework opens a new flow"
    gone = [{"kind": "dossier", "approval": {"option": "accept", "valid_for_current": True, "basis_withdrawn_after_approval": True}}]
    assert _ns(runs=[{"state": "finished"}], reviews=[{"id": "r"}], artifacts=gone)["code"] == "basis_withdrawn"


def test_task_need_mode_follows_the_profile_qualification() -> None:
    unqualified = {"ref": "p@1", "qualification_status": "not_evaluated", "skill_version_refs": ["s@1"]}
    need = task_need_for(unqualified, "pol-1")
    assert need["qualification_mode"] == "sandbox_experiment" and need["skill_version_refs"] == ["s@1"] and need["routing_policy_ref"] == "pol-1"
    assert all(need[k] == v for k, v in TASK_DEFAULTS.items())
    qualified = {"ref": "p@2", "qualification_status": "task_qualified", "qualified_task_classes": ["source_analysis"]}
    assert task_need_for(qualified, "pol-1")["qualification_mode"] == "qualified"
    other_class = {"ref": "p@3", "qualification_status": "task_qualified", "qualified_task_classes": ["evaluation"]}
    assert task_need_for(other_class, "pol-1")["qualification_mode"] == "sandbox_experiment", "qualified for another class is not qualified here"
