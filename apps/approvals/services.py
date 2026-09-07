from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.access_control.models import User, UserRole
from apps.approvals.models import Approval, ApprovalHistory
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.services import user_can_view_project_id
from apps.surveys.models import Survey, SurveyStatus
from apps.surveys.services import (
    get_survey_workflow_snapshot,
    lock_survey_for_workflow,
    update_locked_survey_workflow_state,
    validate_survey_readiness,
    validate_survey_ready_for_submission,
)


class ApprovalConflictError(Exception):
    pass


@dataclass(frozen=True)
class ApprovalSummary:
    survey_id: int
    current_status: str
    submitted_at: object
    submitted_by: int | None
    approved_at: object
    approved_by: int | None
    rejection_reason: str | None
    history: list[dict[str, object]]


def submit_survey_for_approval(*, actor: User, survey: Survey) -> None:
    with transaction.atomic():
        locked_survey = lock_survey_for_workflow(survey_id=survey.pk)
        _validate_submit_actor(actor=actor, survey=locked_survey)
        validate_survey_ready_for_submission(survey=locked_survey)
        if Approval.objects.filter(survey_id=locked_survey.id).exists():
            raise ValidationError("Approval already exists for this survey.")

        approval = Approval.objects.create(
            survey_id=locked_survey.id,
            submitted_at=timezone.now(),
            submitted_by=actor,
        )
        update_locked_survey_workflow_state(
            survey_id=locked_survey.id,
            expected_statuses={SurveyStatus.READY},
            status=SurveyStatus.PENDING_APPROVAL,
        )
        ApprovalHistory.objects.create(approval=approval, action="submitted", actor=actor)
        record_audit_event(
            action=AuditAction.SURVEY_SUBMITTED,
            entity_type="survey",
            entity_id=locked_survey.id,
            user=actor,
            project_id=locked_survey.project_id,
            survey_id=locked_survey.id,
        )


def approve_survey(*, actor: User, survey: Survey) -> None:
    with transaction.atomic():
        locked_survey = lock_survey_for_workflow(survey_id=survey.pk)
        _validate_review_actor(actor=actor, survey=locked_survey)
        approval = _require_pending_approval(survey=locked_survey)
        validate_survey_readiness(survey=locked_survey)
        update_locked_survey_workflow_state(
            survey_id=locked_survey.id,
            expected_statuses={SurveyStatus.PENDING_APPROVAL},
            status=SurveyStatus.APPROVED,
            approved_by_id=actor.pk,
            update_approved_by=True,
        )
        approval.approved_at = timezone.now()
        approval.approved_by = actor
        approval.rejection_reason = None
        approval.save(update_fields=["approved_at", "approved_by", "rejection_reason", "updated_at"])
        ApprovalHistory.objects.create(approval=approval, action="approved", actor=actor)
        record_audit_event(
            action=AuditAction.APPROVAL_APPROVED,
            entity_type="survey",
            entity_id=locked_survey.id,
            user=actor,
            project_id=locked_survey.project_id,
            survey_id=locked_survey.id,
        )


def reject_survey(*, actor: User, survey: Survey, reason: str) -> None:
    with transaction.atomic():
        locked_survey = lock_survey_for_workflow(survey_id=survey.pk)
        _validate_review_actor(actor=actor, survey=locked_survey)
        approval = _require_pending_approval(survey=locked_survey)
        update_locked_survey_workflow_state(
            survey_id=locked_survey.id,
            expected_statuses={SurveyStatus.PENDING_APPROVAL},
            status=SurveyStatus.REJECTED,
            approved_by_id=None,
            update_approved_by=True,
        )
        approval.approved_at = None
        approval.approved_by = None
        approval.rejection_reason = reason
        approval.save(update_fields=["approved_at", "approved_by", "rejection_reason", "updated_at"])
        ApprovalHistory.objects.create(approval=approval, action="rejected", actor=actor, reason=reason)
        record_audit_event(
            action=AuditAction.APPROVAL_REJECTED,
            entity_type="survey",
            entity_id=locked_survey.id,
            user=actor,
            project_id=locked_survey.project_id,
            survey_id=locked_survey.id,
        )


def get_approval_summary(*, survey: Survey) -> ApprovalSummary:
    workflow = get_survey_workflow_snapshot(survey_id=survey.pk)
    approval = Approval.objects.get(survey_id=workflow.id)
    history = [
        {"action": row["action"], "actor_id": row["actor_id"], "timestamp": row["timestamp"]}
        for row in ApprovalHistory.objects.filter(approval_id=approval.pk)
        .order_by("timestamp", "id")
        .values("action", "actor_id", "timestamp")
    ]
    return ApprovalSummary(
        survey_id=workflow.id,
        current_status=workflow.status,
        submitted_at=approval.submitted_at,
        submitted_by=approval.submitted_by_id,
        approved_at=approval.approved_at,
        approved_by=approval.approved_by_id,
        rejection_reason=approval.rejection_reason,
        history=history,
    )


def get_survey_rejection_reason(*, survey_id: int) -> str | None:
    return Approval.objects.filter(survey_id=survey_id).values_list(
        "rejection_reason", flat=True
    ).first()


def _validate_submit_actor(*, actor: User, survey) -> None:
    if not actor.is_active or actor.role != UserRole.SURVEY_ENGINEER:
        raise PermissionDenied(
            "Only an active assigned survey engineer can submit this survey for approval."
        )
    if not user_can_view_project_id(user=actor, project_id=survey.project_id):
        raise PermissionDenied(
            "Only an active assigned survey engineer can submit this survey for approval."
        )


def _validate_review_actor(*, actor: User, survey) -> None:
    if not actor.is_active:
        raise PermissionDenied(
            "Only an active administrator or the owning project manager can review this survey."
        )
    allowed = actor.role == UserRole.ADMINISTRATOR or (
        actor.role == UserRole.PROJECT_MANAGER and survey.project_manager_id == actor.pk
    )
    if not allowed:
        raise PermissionDenied(
            "Only an active administrator or the owning project manager can review this survey."
        )
    if survey.created_by_id == actor.pk:
        raise PermissionDenied("You cannot approve or reject a survey you created.")


def _require_pending_approval(*, survey) -> Approval:
    if survey.status != SurveyStatus.PENDING_APPROVAL:
        raise ApprovalConflictError("Survey must be pending approval.")
    try:
        return Approval.objects.get(survey_id=survey.id)
    except Approval.DoesNotExist as exc:
        raise ValidationError("Approval does not exist for this survey.") from exc
