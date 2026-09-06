from django.core.exceptions import ValidationError

from apps.approvals.models import Approval, ApprovalHistory


def get_approval_for_survey(*, survey_id):
    try:
        return Approval.objects.get(survey_id=survey_id)
    except Approval.DoesNotExist as exc:
        raise ValidationError("Approval does not exist for this survey.") from exc


def record_survey_archived_history(*, survey_id, actor):
    ApprovalHistory.objects.create(
        approval=get_approval_for_survey(survey_id=survey_id),
        action="archived",
        actor=actor,
    )
