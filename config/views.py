"""Template-only views for the Step 1 through Step 4 route shells."""

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def foundation_preview(request):
    """Render static UI examples without reading models or calling APIs."""
    return render(
        request,
        "foundation_preview.html",
        {
            "active_nav": "projects",
            "page_title": "Dashboard foundation | Hitech Drone Mapping",
            "preview_table_headings": ["Column one", "Column two", "Column three"],
        },
    )


@ensure_csrf_cookie
def login_view(request):
    """Render the public login page for the external Hitech Auth Service flow."""
    return render(
        request,
        "login.html",
        {
            "page_title": "Login | Hitech Drone Mapping",
            "hitech_auth_login_url": getattr(settings, "HITECH_AUTH_LOGIN_URL", None),
            "demo_auth_enabled": settings.ENABLE_DEMO_AUTH,
            "demo_auth_roles": [
                {"key": "administrator", "label": "Administrator"},
                {"key": "project_manager", "label": "Project Manager"},
                {"key": "survey_engineer", "label": "Survey Engineer"},
                {"key": "viewer", "label": "Viewer"},
            ],
        },
    )


@ensure_csrf_cookie
def projects_view(request):
    """Render the projects dashboard shell without querying models or APIs."""
    return render(
        request,
        "projects.html",
        {
            "active_nav": "projects",
            "page_title": "Projects | Hitech Drone Mapping",
            "project_table_headings": [
                "Project name",
                "Location",
                "Status",
                "Site count",
                "Last survey date",
                "Open",
            ],
            "project_table_rows": [],
            "ui_user_name": "Signed-in user",
            "ui_user_role": "Role pending Auth Service",
            "ui_user_initials": "HS",
            "ui_user_is_admin": False,
            "ui_can_create_project": False,
            "ui_show_assigned_projects_hint": False,
        },
    )


@ensure_csrf_cookie
def project_detail_view(request, id):
    """Render the project detail shell without querying models or APIs."""
    return render(
        request,
        "project_detail.html",
        {
            "active_nav": "projects",
            "page_title": f"Project {id} | Hitech Drone Mapping",
            "project_id": id,
            "project_site_table_headings": [
                "Site name",
                "Coordinates",
                "CRS",
                "Survey count",
                "Open site",
            ],
            "project_site_table_rows": [],
            "ui_user_name": "Signed-in user",
            "ui_user_role": "Role pending Auth Service",
            "ui_user_initials": "HS",
            "ui_user_is_admin": False,
            "ui_can_edit_project": False,
            "ui_can_add_site": False,
        },
    )


@ensure_csrf_cookie
def site_detail_view(request, id, site_id):
    """Render the site detail shell without querying models or APIs."""
    return render(
        request,
        "site_detail.html",
        {
            "active_nav": "projects",
            "page_title": f"Site {site_id} | Hitech Drone Mapping",
            "project_id": id,
            "site_id": site_id,
            "site_survey_table_headings": [
                "Survey name",
                "Survey date",
                "Status",
                "Processing status",
                "Open survey",
            ],
            "site_survey_table_rows": [],
            "ui_user_name": "Signed-in user",
            "ui_user_role": "Role pending Auth Service",
            "ui_user_initials": "HS",
            "ui_user_is_admin": False,
            "ui_can_create_survey": False,
        },
    )


@ensure_csrf_cookie
def survey_detail_view(request, id):
    """Render the survey workspace shell without querying models or APIs."""
    return render(
        request,
        "survey_detail.html",
        {
            "active_nav": "projects",
            "page_title": f"Survey {id} | Hitech Drone Mapping",
            "survey_id": id,
            "ui_user_name": "Signed-in user",
            "ui_user_role": "Role pending Auth Service",
            "ui_user_initials": "HS",
            "ui_user_is_admin": False,
        },
    )
