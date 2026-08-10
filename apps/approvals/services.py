from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.access_control.models import User, UserRole
from apps.approvals.models import Approval, ApprovalHistory
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.services import user_can_view_project
from apps.surveys.models import Survey, SurveyStatus
from apps.surveys.services import (
    get_locked_survey_for_workflow,
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
    _validate_submit_actor(actor=actor, survey=survey)

    with transaction.atomic():
        locked_survey = get_locked_survey_for_workflow(survey_id=survey.pk)
        validate_survey_ready_for_submission(survey=locked_survey)

        try:
            locked_survey.approval
        except ObjectDoesNotExist:
            pass
        else:
            raise ValidationError("Approval already exists for this survey.")

        submitted_at = timezone.now()
        approval = Approval.objects.create(
            survey=locked_survey,
            submitted_at=submitted_at,
            submitted_by=actor,
        )
        locked_survey.status = SurveyStatus.PENDING_APPROVAL
        locked_survey.save(update_fields=["status", "updated_at"])
        ApprovalHistory.objects.create(
            approval=approval,
            action="submitted",
            actor=actor,
        )
        record_audit_event(
            action=AuditAction.SURVEY_SUBMITTED,
            entity_type="survey",
            entity_id=locked_survey.pk,
            user=actor,
            project=locked_survey.project,
            survey=locked_survey,
        )


def approve_survey(*, actor: User, survey: Survey) -> None:
    _validate_review_actor(actor=actor, survey=survey)

    with transaction.atomic():
        locked_survey = get_locked_survey_for_workflow(survey_id=survey.pk)
        approval = _require_pending_approval(survey=locked_survey)
        validate_survey_readiness(survey=locked_survey)

        approved_at = timezone.now()
        locked_survey.status = SurveyStatus.APPROVED
        locked_survey.approved_by = actor
        locked_survey.save(update_fields=["status", "approved_by", "updated_at"])
        approval.approved_at = approved_at
        approval.approved_by = actor
        approval.rejection_reason = None
        approval.save(update_fields=["approved_at", "approved_by", "rejection_reason", "updated_at"])
        ApprovalHistory.objects.create(
            approval=approval,
            action="approved",
            actor=actor,
        )
        record_audit_event(
            action=AuditAction.APPROVAL_APPROVED,
            entity_type="survey",
            entity_id=locked_survey.pk,
            user=actor,
            project=locked_survey.project,
            survey=locked_survey,
        )


def reject_survey(*, actor: User, survey: Survey, reason: str) -> None:
    _validate_review_actor(actor=actor, survey=survey)

    with transaction.atomic():
        locked_survey = get_locked_survey_for_workflow(survey_id=survey.pk)
        approval = _require_pending_approval(survey=locked_survey)

        locked_survey.status = SurveyStatus.REJECTED
        locked_survey.approved_by = None
        locked_survey.save(update_fields=["status", "approved_by", "updated_at"])
        approval.approved_at = None
        approval.approved_by = None
        approval.rejection_reason = reason
        approval.save(update_fields=["approved_at", "approved_by", "rejection_reason", "updated_at"])
        ApprovalHistory.objects.create(
            approval=approval,
            action="rejected",
            actor=actor,
            reason=reason,
        )
        record_audit_event(
            action=AuditAction.APPROVAL_REJECTED,
            entity_type="survey",
            entity_id=locked_survey.pk,
            user=actor,
            project=locked_survey.project,
            survey=locked_survey,
        )


def get_approval_summary(*, survey: Survey) -> ApprovalSummary:
    try:
        approval = (
            Approval.objects.select_related("submitted_by", "approved_by")
            .prefetch_related("history__actor")
            .get(survey=survey)
        )
    except Approval.DoesNotExist:
        raise

    history = [
        {
            "action": entry.action,
            "actor_id": entry.actor_id,
            "timestamp": entry.timestamp,
        }
        for entry in approval.history.order_by("timestamp", "id")
    ]

    return ApprovalSummary(
        survey_id=survey.pk,
        current_status=survey.status,
        submitted_at=approval.submitted_at,
        submitted_by=approval.submitted_by_id,
        approved_at=approval.approved_at,
        approved_by=approval.approved_by_id,
        rejection_reason=approval.rejection_reason,
        history=history,
    )


def _validate_submit_actor(*, actor: User, survey: Survey) -> None:
    if not actor.is_active or actor.role != UserRole.SURVEY_ENGINEER:
        raise PermissionDenied(
            "Only an active assigned survey engineer can submit this survey for approval."
        )

    if not user_can_view_project(actor, survey.project):
        raise PermissionDenied(
            "Only an active assigned survey engineer can submit this survey for approval."
        )


def _validate_review_actor(*, actor: User, survey: Survey) -> None:
    if not actor.is_active:
        raise PermissionDenied(
            "Only an active administrator or the owning project manager can review this survey."
        )

    if actor.role == UserRole.ADMINISTRATOR:
        allowed = True
    elif actor.role == UserRole.PROJECT_MANAGER:
        allowed = survey.project.project_manager_id == actor.pk
    else:
        allowed = False

    if not allowed:
        raise PermissionDenied(
            "Only an active administrator or the owning project manager can review this survey."
        )

    if survey.created_by_id == actor.pk:
        raise PermissionDenied("You cannot approve or reject a survey you created.")


def _require_pending_approval(*, survey: Survey) -> Approval:
    if survey.status != SurveyStatus.PENDING_APPROVAL:
        raise ApprovalConflictError("Survey must be pending approval.")

    try:
        return survey.approval
    except ObjectDoesNotExist as exc:
        raise ValidationError("Approval does not exist for this survey.") from exc
