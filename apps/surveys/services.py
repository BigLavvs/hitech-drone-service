from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.models import Project, Site
from apps.projects.services import user_can_manage_project, user_can_view_project
from apps.surveys.models import Survey, SurveyStatus

_UNSET = object()


def get_surveys_visible_to_user(*, user: User):
    queryset = (
        Survey.objects.select_related(
            "project",
            "site",
            "created_by",
            "approved_by",
            "approval",
        )
        .order_by("-survey_date", "-id")
    )

    if not user.is_active:
        return queryset.none()

    if user.role == UserRole.ADMINISTRATOR:
        return queryset

    if user.role == UserRole.PROJECT_MANAGER:
        return queryset.filter(project__project_manager=user)

    if user.role in {UserRole.SURVEY_ENGINEER, UserRole.VIEWER}:
        return queryset.filter(project__memberships__user=user).distinct()

    return queryset.none()


def get_survey_visible_to_user(*, user: User, survey_id: int) -> Survey:
    survey = (
        Survey.objects.select_related(
            "project",
            "site",
            "created_by",
            "approved_by",
            "approval",
        )
        .filter(pk=survey_id)
        .first()
    )
    if survey is None:
        raise Survey.DoesNotExist

    if not user_can_view_project(user, survey.project):
        raise PermissionDenied("You do not have permission to access this survey.")

    return survey


def create_survey(
    *,
    actor: User,
    project: Project,
    site: Site,
    name: str,
    survey_date,
    drone_model: str | None | object = _UNSET,
    pilot: str | None | object = _UNSET,
    coordinate_reference_system: str | object = _UNSET,
    notes: str | None | object = _UNSET,
) -> Survey:
    _validate_survey_create_actor(actor=actor, project=project)

    if project.status != "active":
        raise ValidationError("Only active projects can have surveys created.")

    if site.project_id != project.pk:
        raise ValidationError("Site must belong to the supplied project.")

    survey = Survey(
        project=project,
        site=site,
        name=name,
        survey_date=survey_date,
        created_by=actor,
    )

    for field_name, value in (
        ("drone_model", drone_model),
        ("pilot", pilot),
        ("coordinate_reference_system", coordinate_reference_system),
        ("notes", notes),
    ):
        if value is not _UNSET:
            setattr(survey, field_name, value)

    survey.full_clean()

    with transaction.atomic():
        survey.save()
        record_audit_event(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=survey.pk,
            user=actor,
            project=survey.project,
            survey=survey,
        )

    return survey


def update_survey(
    *,
    actor: User,
    survey: Survey,
    name: str | object = _UNSET,
    survey_date: object = _UNSET,
    drone_model: str | None | object = _UNSET,
    pilot: str | None | object = _UNSET,
    coordinate_reference_system: str | object = _UNSET,
    notes: str | None | object = _UNSET,
) -> Survey:
    _validate_survey_update_actor(actor=actor, survey=survey)

    if survey.project.status != "active":
        raise ValidationError("Only active projects can have surveys updated.")

    update_fields: list[str] = []

    for field_name, value in (
        ("name", name),
        ("survey_date", survey_date),
        ("drone_model", drone_model),
        ("pilot", pilot),
        ("coordinate_reference_system", coordinate_reference_system),
        ("notes", notes),
    ):
        if value is not _UNSET:
            setattr(survey, field_name, value)
            update_fields.append(field_name)

    if not update_fields:
        return survey

    survey.full_clean()

    with transaction.atomic():
        survey.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=AuditAction.SURVEY_UPDATED,
            entity_type="survey",
            entity_id=survey.pk,
            user=actor,
            project=survey.project,
            survey=survey,
        )

    return survey


def get_locked_survey_for_workflow(*, survey_id: int) -> Survey:
    return (
        Survey.objects.select_for_update()
        .select_related("project", "site")
        .get(pk=survey_id)
    )


def validate_survey_ready_for_submission(*, survey: Survey) -> None:
    if survey.status != SurveyStatus.READY:
        raise ValidationError("Survey is not ready for approval.")

    validate_survey_readiness(survey=survey)


def validate_survey_readiness(*, survey: Survey) -> None:
    if survey.processing_status != "completed":
        raise ValidationError("Survey processing is not complete.")

    files = list(survey.files.select_related("processing_job").all())
    if not files:
        raise ValidationError("Survey must have at least one file before submission.")

    for survey_file in files:
        if survey_file.status != "ready":
            raise ValidationError("All survey files must be ready before submission.")

        try:
            processing_job = survey_file.processing_job
        except ObjectDoesNotExist as exc:
            raise ValidationError(
                "All survey files must have a completed processing job before submission."
            ) from exc

        if processing_job.status != "completed":
            raise ValidationError(
                "All survey files must have a completed processing job before submission."
            )


def archive_survey_after_review(*, actor: User, survey: Survey) -> Survey:
    from apps.approvals.models import ApprovalHistory

    if not actor.is_active:
        raise PermissionDenied(
            "Only an active administrator or the owning project manager can archive this survey."
        )

    if actor.role == UserRole.ADMINISTRATOR:
        allowed = True
    elif actor.role == UserRole.PROJECT_MANAGER:
        allowed = survey.project.project_manager_id == actor.pk
    else:
        allowed = False

    if not allowed:
        raise PermissionDenied(
            "Only an active administrator or the owning project manager can archive this survey."
        )

    with transaction.atomic():
        locked_survey = get_locked_survey_for_workflow(survey_id=survey.pk)
        if locked_survey.status not in {SurveyStatus.APPROVED, SurveyStatus.REJECTED}:
            raise ValidationError("Only approved or rejected surveys can be archived.")

        try:
            approval = locked_survey.approval
        except ObjectDoesNotExist as exc:
            raise ValidationError("Approval does not exist for this survey.") from exc

        locked_survey.status = SurveyStatus.ARCHIVED
        locked_survey.save(update_fields=["status", "updated_at"])
        ApprovalHistory.objects.create(
            approval=approval,
            action="archived",
            actor=actor,
        )
        record_audit_event(
            action=AuditAction.SURVEY_ARCHIVED,
            entity_type="survey",
            entity_id=locked_survey.pk,
            user=actor,
            project=locked_survey.project,
            survey=locked_survey,
        )

    return Survey.objects.select_related("approval").get(pk=survey.pk)


def _validate_survey_create_actor(*, actor: User, project: Project) -> None:
    if user_can_manage_project(actor, project):
        return

    if (
        actor.is_active
        and actor.role == UserRole.SURVEY_ENGINEER
        and user_can_view_project(actor, project)
    ):
        return

    raise PermissionDenied(
        "Only active administrators, the owning project manager, and assigned survey engineers can create surveys."
    )


def _validate_survey_update_actor(*, actor: User, survey: Survey) -> None:
    if user_can_manage_project(actor, survey.project):
        return

    if (
        actor.is_active
        and actor.role == UserRole.SURVEY_ENGINEER
        and survey.created_by_id == actor.pk
        and user_can_view_project(actor, survey.project)
    ):
        return

    raise PermissionDenied(
        "Only active administrators, the owning project manager, and the assigned creator survey engineer can update survey metadata."
    )
