"""导入任务状态机（规格第 4 节）的纯单元契约测试。

覆盖：

- 合法迁移集合与规格逐字一致（终态无出边）。
- 全部 10x10 状态对的判定：非法迁移必须抛 ``ImportJobStateConflictError``，
  错误码固定 ``import_job_state_conflict``。
- 取消许可集合、Worker 领取目标状态映射。
- ``publish_failed -> publishing`` 的唯一重试回边。

HTTP 层的 409 映射与数据库层的一致性由 ``test_import_jobs_api.py`` /
``test_import_jobs_postgres.py`` 覆盖。
"""

from __future__ import annotations

import pytest

from app.repositories.import_job_repository import (
    ALLOWED_JOB_TRANSITIONS,
    CANCELLABLE_JOB_STATUSES,
    CLAIM_TARGET_STATUS,
    CLAIMABLE_JOB_STATUSES,
    DECISION_TO_STATUS,
    IMPORT_JOB_STATUSES,
    WORKING_JOB_STATUSES,
    ImportJobStateConflictError,
    validate_job_transition,
)

SPEC_TRANSITIONS = {
    "submitted": {"validating", "cancelled"},
    "validating": {"validation_failed", "approval_pending", "cancelled"},
    "validation_failed": set(),
    "approval_pending": {"approved", "rejected", "cancelled"},
    "approved": {"publishing"},
    "rejected": set(),
    "publishing": {"published", "publish_failed"},
    "publish_failed": {"publishing"},
    "published": set(),
    "cancelled": set(),
}


def test_status_machine_matches_spec_transitions_exactly() -> None:
    assert set(ALLOWED_JOB_TRANSITIONS) == set(SPEC_TRANSITIONS)
    assert {
        status: set(targets) for status, targets in ALLOWED_JOB_TRANSITIONS.items()
    } == SPEC_TRANSITIONS
    assert set(IMPORT_JOB_STATUSES) == set(SPEC_TRANSITIONS)


def test_terminal_statuses_have_no_outgoing_transition() -> None:
    for status in ("validation_failed", "rejected", "published", "cancelled"):
        assert ALLOWED_JOB_TRANSITIONS[status] == frozenset()


@pytest.mark.parametrize("current", sorted(SPEC_TRANSITIONS))
@pytest.mark.parametrize("target", sorted(SPEC_TRANSITIONS))
def test_every_transition_pair_is_classified_by_the_spec(current, target) -> None:
    if target in SPEC_TRANSITIONS[current]:
        validate_job_transition(current, target)
        return
    with pytest.raises(ImportJobStateConflictError) as conflict:
        validate_job_transition(current, target)
    assert conflict.value.code == "import_job_state_conflict"
    assert conflict.value.details == {
        "current_status": current,
        "target_status": target,
    }


def test_unknown_status_cannot_transition_anywhere() -> None:
    for target in SPEC_TRANSITIONS:
        with pytest.raises(ImportJobStateConflictError):
            validate_job_transition("not_a_status", target)


def test_cancellable_statuses_match_spec() -> None:
    assert set(CANCELLABLE_JOB_STATUSES) == {
        "submitted",
        "validating",
        "approval_pending",
    }
    for status in CANCELLABLE_JOB_STATUSES:
        assert "cancelled" in ALLOWED_JOB_TRANSITIONS[status]
    for status in ("approved", "publishing", "published", "publish_failed"):
        assert "cancelled" not in ALLOWED_JOB_TRANSITIONS[status]


def test_worker_claim_targets_and_working_statuses() -> None:
    assert set(CLAIMABLE_JOB_STATUSES) == {"submitted", "approved"}
    assert set(WORKING_JOB_STATUSES) == {"validating", "publishing"}
    assert CLAIM_TARGET_STATUS == {
        "submitted": "validating",
        "approved": "publishing",
    }
    for source, target in CLAIM_TARGET_STATUS.items():
        assert target in ALLOWED_JOB_TRANSITIONS[source]


def test_validation_completion_targets_are_reachable_from_validating() -> None:
    assert "approval_pending" in ALLOWED_JOB_TRANSITIONS["validating"]
    assert "validation_failed" in ALLOWED_JOB_TRANSITIONS["validating"]
    assert "publish_failed" in ALLOWED_JOB_TRANSITIONS["publishing"]
    assert "published" in ALLOWED_JOB_TRANSITIONS["publishing"]


def test_publish_failed_is_the_only_retry_back_edge() -> None:
    assert ALLOWED_JOB_TRANSITIONS["publish_failed"] == frozenset({"publishing"})
    assert DECISION_TO_STATUS == {"approve": "approved", "reject": "rejected"}
    for target in DECISION_TO_STATUS.values():
        assert target in ALLOWED_JOB_TRANSITIONS["approval_pending"]
