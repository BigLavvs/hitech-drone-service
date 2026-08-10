"""URL configuration for the Step 1 through Step 23 routes."""

from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.access_control.views import (
    AuthValidateView,
    DemoSessionCreateView,
    UserDetailAPIView,
    UserListCreateAPIView,
    admin_panel_view,
)
from apps.approvals.views import (
    SurveyApprovalDetailAPIView,
    SurveyApproveAPIView,
    SurveyArchiveAPIView,
    SurveyRejectAPIView,
    SurveySubmitAPIView,
)
from apps.audit.views import AuditLogDetailAPIView, AuditLogListAPIView
from apps.files.views import SurveyFileDownloadAPIView, SurveyFileListCreateAPIView
from apps.maps.views import (
    MapLayerTileRedirectAPIView,
    SurveyMapLayerListAPIView,
    SurveyMeasurementDetailAPIView,
    SurveyMeasurementListCreateAPIView,
)
from apps.models3d.views import SurveyModelListAPIView
from apps.processing.views import ProcessingJobDetailAPIView, ProcessingJobRetryAPIView
from apps.projects.views import (
    ProjectDetailAPIView,
    ProjectListCreateAPIView,
    SiteDetailAPIView,
    SiteListCreateAPIView,
)
from apps.surveys.views import SurveyDetailAPIView, SurveyListCreateAPIView
from config.health import health_view, ready_view
from config.views import (
    foundation_preview,
    login_view,
    project_detail_view,
    projects_view,
    survey_detail_view,
    site_detail_view,
)


urlpatterns = [
    path("", foundation_preview, name="foundation-preview"),
    path("health", health_view, name="health"),
    path("ready", ready_view, name="ready"),
    path("api/schema", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    path("docs/redoc", SpectacularRedocView.as_view(url_name="api-schema"), name="api-redoc"),
    path("api/v1/auth/validate", AuthValidateView.as_view(), name="api-auth-validate"),
    path(
        "api/v1/demo-auth/session",
        DemoSessionCreateView.as_view(),
        name="api-demo-auth-session",
    ),
    path("api/v1/users", UserListCreateAPIView.as_view(), name="api-user-list"),
    path("api/v1/users/<int:user_id>", UserDetailAPIView.as_view(), name="api-user-detail"),
    path("api/v1/audit-logs", AuditLogListAPIView.as_view(), name="api-audit-log-list"),
    path(
        "api/v1/audit-logs/<int:audit_log_id>",
        AuditLogDetailAPIView.as_view(),
        name="api-audit-log-detail",
    ),
    path("api/v1/projects", ProjectListCreateAPIView.as_view(), name="api-project-list"),
    path(
        "api/v1/projects/<int:project_id>",
        ProjectDetailAPIView.as_view(),
        name="api-project-detail",
    ),
    path(
        "api/v1/projects/<int:project_id>/sites",
        SiteListCreateAPIView.as_view(),
        name="api-site-list",
    ),
    path(
        "api/v1/projects/<int:project_id>/sites/<int:site_id>",
        SiteDetailAPIView.as_view(),
        name="api-site-detail",
    ),
    path("api/v1/surveys", SurveyListCreateAPIView.as_view(), name="api-survey-list"),
    path(
        "api/v1/surveys/<int:survey_id>",
        SurveyDetailAPIView.as_view(),
        name="api-survey-detail",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/submit",
        SurveySubmitAPIView.as_view(),
        name="api-survey-submit",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/approve",
        SurveyApproveAPIView.as_view(),
        name="api-survey-approve",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/reject",
        SurveyRejectAPIView.as_view(),
        name="api-survey-reject",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/archive",
        SurveyArchiveAPIView.as_view(),
        name="api-survey-archive",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/approvals",
        SurveyApprovalDetailAPIView.as_view(),
        name="api-survey-approvals",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/files",
        SurveyFileListCreateAPIView.as_view(),
        name="api-survey-files",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/files/<int:file_id>/download",
        SurveyFileDownloadAPIView.as_view(),
        name="api-survey-file-download",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/map-layers",
        SurveyMapLayerListAPIView.as_view(),
        name="api-survey-map-layers",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/measurements",
        SurveyMeasurementListCreateAPIView.as_view(),
        name="api-survey-measurement-list",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/measurements/<int:measurement_id>",
        SurveyMeasurementDetailAPIView.as_view(),
        name="api-survey-measurement-detail",
    ),
    path(
        "api/v1/map-layers/<int:file_id>/tiles/<int:z>/<int:x>/<int:y>",
        MapLayerTileRedirectAPIView.as_view(),
        name="api-map-layer-tile",
    ),
    path(
        "api/v1/surveys/<int:survey_id>/models",
        SurveyModelListAPIView.as_view(),
        name="api-survey-models",
    ),
    path(
        "api/v1/processing-jobs/<int:processing_job_id>",
        ProcessingJobDetailAPIView.as_view(),
        name="api-processing-job-detail",
    ),
    path(
        "api/v1/processing-jobs/<int:processing_job_id>/retry",
        ProcessingJobRetryAPIView.as_view(),
        name="api-processing-job-retry",
    ),
    path("login", login_view, name="login"),
    path("admin", admin_panel_view, name="admin-panel"),
    path("projects", projects_view, name="projects"),
    path("projects/<int:id>", project_detail_view, name="project-detail"),
    path("projects/<int:id>/sites/<int:site_id>", site_detail_view, name="site-detail"),
    path("surveys/<int:id>", survey_detail_view, name="survey-detail"),
]
