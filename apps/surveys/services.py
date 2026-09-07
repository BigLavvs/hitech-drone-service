from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction
from apps.audit.services import record_audit_event
from apps.projects.models import Project, Site
from apps.projects.services import (
    get_project_access_snapshot,
    get_project_ids_visible_to_user,
    lock_project_for_update,
    lock_site_for_update,
    user_can_manage_project,
    user_can_manage_project_id,
    user_can_view_project,
    user_can_view_project_id,
)
from apps.surveys.models import Survey, SurveyStatus

_UNSET = object()


@dataclass(frozen=True)
class SurveyWorkflowSnapshot:
    id: int
    project_id: int
    project_status: str
    project_manager_id: int | None
    created_by_id: int | None
    status: str
    processing_status: str

    @property
    def pk(self):
        return self.id


def _survey_workflow_snapshot(survey) -> SurveyWorkflowSnapshot:
    project = get_project_access_snapshot(project_id=survey.project_id)
    return SurveyWorkflowSnapshot(
        id=survey.pk,
        project_id=survey.project_id,
        project_status=project.status,
        project_manager_id=project.project_manager_id,
        created_by_id=survey.created_by_id,
        status=survey.status,
        processing_status=survey.processing_status,
    )


def get_survey_workflow_snapshot(*, survey_id: int) -> SurveyWorkflowSnapshot:
    row = Survey.objects.filter(pk=survey_id).values(
        "id", "project_id", "created_by_id", "status", "processing_status",
    ).get()
    project = get_project_access_snapshot(project_id=row["project_id"])
    return SurveyWorkflowSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        project_status=project.status,
        project_manager_id=project.project_manager_id,
        created_by_id=row["created_by_id"],
        status=row["status"],
        processing_status=row["processing_status"],
    )


def lock_survey_for_workflow(*, survey_id: int) -> SurveyWorkflowSnapshot:
    # Project ownership/archive changes and survey mutations share this lock
    # order: project -> survey. Authorization is evaluated from the locked
    # rows, never from a caller-provided snapshot.
    survey_reference = Survey.objects.only("project_id").get(pk=survey_id)
    project = lock_project_for_update(
        project_id=survey_reference.project_id,
        fields=("id", "status", "project_manager_id"),
    )
    row = Survey.objects.select_for_update().filter(pk=survey_id).values(
        "id", "project_id", "created_by_id", "status", "processing_status",
    ).get()
    return SurveyWorkflowSnapshot(
        id=row["id"],
        project_id=row["project_id"],
        project_status=project.status,
        project_manager_id=project.project_manager_id,
        created_by_id=row["created_by_id"],
        status=row["status"],
        processing_status=row["processing_status"],
    )


def update_locked_survey_workflow_state(
    *,
    survey_id: int,
    expected_statuses: set[str],
    status: str,
    approved_by_id: int | None = None,
    update_approved_by: bool = False,
) -> SurveyWorkflowSnapshot:
    survey = Survey.objects.select_for_update().get(pk=survey_id)
    if survey.status not in expected_statuses:
        raise ValidationError("Survey is not in an eligible workflow state.")
    survey.status = status
    update_fields = ["status", "updated_at"]
    if update_approved_by:
        survey.approved_by_id = approved_by_id
        update_fields.append("approved_by")
    survey.save(update_fields=update_fields)
    return get_survey_workflow_snapshot(survey_id=survey_id)


def update_survey_processing_state(
    *, survey_id: int, status: str, processing_status: str
) -> SurveyWorkflowSnapshot:
    survey = Survey.objects.get(pk=survey_id)
    survey.status = status
    survey.processing_status = processing_status
    survey.save(update_fields=["status", "processing_status", "updated_at"])
    return get_survey_workflow_snapshot(survey_id=survey_id)


def update_locked_survey_processing_state(
    *, survey_id: int, status: str, processing_status: str
) -> SurveyWorkflowSnapshot:
    """Persist processing state while retaining the survey coordination lock."""
    survey = Survey.objects.select_for_update().get(pk=survey_id)
    survey.status = status
    survey.processing_status = processing_status
    survey.save(update_fields=["status", "processing_status", "updated_at"])
    return _survey_workflow_snapshot(survey)


def get_surveys_visible_to_user(*, user: User, filters=None):
    queryset = (
        Survey.objects
        .order_by("-survey_date", "-id")
    )

    if not user.is_active:
        return queryset.none()

    if user.role == UserRole.ADMINISTRATOR:
        pass
    elif user.role in {
        UserRole.PROJECT_MANAGER,
        UserRole.SURVEY_ENGINEER,
        UserRole.VIEWER,
    }:
        queryset = queryset.filter(project_id__in=get_project_ids_visible_to_user(user=user))
    else:
        return queryset.none()

    filters = filters or {}
    for field_name in ("project_id", "site_id", "status"):
        if field_name in filters:
            queryset = queryset.filter(**{field_name: filters[field_name]})
    if "from_date" in filters:
        queryset = queryset.filter(survey_date__gte=filters["from_date"])
    if filters.get("sort") == "survey_date":
        ordering = ("survey_date", "id") if filters["order"] == "asc" else ("-survey_date", "-id")
        queryset = queryset.order_by(*ordering)
    return queryset


def get_survey_visible_to_user(*, user: User, survey_id: int) -> Survey:
    survey = (
        Survey.objects
        .filter(pk=survey_id)
        .first()
    )
    if survey is None:
        raise Survey.DoesNotExist

    from apps.projects.services import user_can_view_project_id

    if not user_can_view_project_id(user=user, project_id=survey.project_id):
        raise PermissionDenied("You do not have permission to access this survey.")

    return survey


def get_survey_with_project(*, survey_id: int) -> Survey:
    return Survey.objects.get(pk=survey_id)


def get_survey_for_project_site_and_name(*, project, site, name: str) -> Survey | None:
    return Survey.objects.filter(project=project, site=site, name=name).first()


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
    with transaction.atomic():
        locked_project = lock_project_for_update(project_id=project.pk)
        _validate_survey_create_actor(actor=actor, project=locked_project)
        if locked_project.status != "active":
            raise ValidationError("Only active projects can have surveys created.")

        try:
            locked_site = lock_site_for_update(site_id=site.pk)
        except Site.DoesNotExist as exc:
            raise ValidationError("Site does not exist.") from exc
        if locked_site.project_id != locked_project.pk:
            raise ValidationError("Site must belong to the supplied project.")

        survey = Survey(
            project=locked_project,
            site=locked_site,
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
        survey.save()
        record_audit_event(
            action=AuditAction.SURVEY_CREATED,
            entity_type="survey",
            entity_id=survey.pk,
            user=actor,
            project_id=survey.project_id,
            survey_id=survey.pk,
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
    with transaction.atomic():
        workflow = lock_survey_for_workflow(survey_id=survey.pk)
        _validate_survey_update_actor(actor=actor, survey=workflow)
        if workflow.project_status != "active":
            raise ValidationError("Only active projects can have surveys updated.")

        locked_survey = Survey.objects.select_for_update().get(pk=survey.pk)
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
                setattr(locked_survey, field_name, value)
                update_fields.append(field_name)

        if not update_fields:
            return locked_survey

        locked_survey.full_clean()
        locked_survey.save(update_fields=[*update_fields, "updated_at"])
        record_audit_event(
            action=AuditAction.SURVEY_UPDATED,
            entity_type="survey",
            entity_id=locked_survey.pk,
            user=actor,
            project_id=locked_survey.project_id,
            survey_id=locked_survey.pk,
        )

    return locked_survey


def synchronize_demo_survey(*, survey: Survey, creator: User, survey_date) -> Survey:
    """Apply the fixed assessment seed values through the surveys owner service."""
    survey.created_by = creator
    survey.status = SurveyStatus.DRAFT
    survey.processing_status = "pending"
    survey.survey_date = survey_date
    survey.drone_model = "DJI Mavic 3 Enterprise"
    survey.pilot = "Demo Survey Engineer"
    survey.notes = "Development-only draft survey for role-based assessment walkthroughs."
    survey.save(update_fields=[
        "created_by", "status", "processing_status", "survey_date", "drone_model",
        "pilot", "notes", "updated_at",
    ])
    return survey


def validate_survey_ready_for_submission(*, survey: Survey) -> None:
    if survey.status != SurveyStatus.READY:
        raise ValidationError("Survey is not ready for approval.")

    validate_survey_readiness(survey=survey)


def validate_survey_readiness(*, survey: Survey) -> None:
    if survey.processing_status != "completed":
        raise ValidationError("Survey processing is not complete.")

    from apps.files.services import get_survey_file_readiness
    from apps.processing.services import get_survey_job_readiness

    file_readiness = get_survey_file_readiness(survey_id=survey.id)
    job_readiness = get_survey_job_readiness(survey_id=survey.id)
    if file_readiness.file_count == 0:
        raise ValidationError("Survey must have at least one file before submission.")
    if not file_readiness.all_files_ready or not job_readiness.all_jobs_completed:
        raise ValidationError("All survey files must have a completed processing job before submission.")


def archive_survey_after_review(*, actor: User, survey: Survey) -> Survey:
    from apps.approvals.interfaces import record_survey_archived_history

    with transaction.atomic():
        locked_survey = lock_survey_for_workflow(survey_id=survey.pk)
        if not actor.is_active or not (
            actor.role == UserRole.ADMINISTRATOR
            or (
                actor.role == UserRole.PROJECT_MANAGER
                and locked_survey.project_manager_id == actor.pk
            )
        ):
            raise PermissionDenied(
                "Only an active administrator or the owning project manager can archive this survey."
            )
        if locked_survey.status not in {SurveyStatus.APPROVED, SurveyStatus.REJECTED}:
            raise ValidationError("Only approved or rejected surveys can be archived.")

        update_locked_survey_workflow_state(
            survey_id=locked_survey.id,
            expected_statuses={SurveyStatus.APPROVED, SurveyStatus.REJECTED},
            status=SurveyStatus.ARCHIVED,
        )
        record_survey_archived_history(survey_id=locked_survey.pk, actor=actor)
        record_audit_event(
            action=AuditAction.SURVEY_ARCHIVED,
            entity_type="survey",
            entity_id=locked_survey.pk,
            user=actor,
            project_id=locked_survey.project_id,
            survey_id=locked_survey.id,
        )

    return Survey.objects.get(pk=survey.pk)


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


def _validate_survey_update_actor(*, actor: User, survey) -> None:
    if user_can_manage_project_id(user=actor, project_id=survey.project_id):
        return

    if (
        actor.is_active
        and actor.role == UserRole.SURVEY_ENGINEER
        and survey.created_by_id == actor.pk
        and user_can_view_project_id(user=actor, project_id=survey.project_id)
    ):
        return

    raise PermissionDenied(
        "Only active administrators, the owning project manager, and the assigned creator survey engineer can update survey metadata."
    )
