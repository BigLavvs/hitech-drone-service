from unittest.mock import patch

from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase

from apps.access_control.models import User, UserRole
from apps.audit.models import AuditAction, AuditLog
from apps.projects.models import Project, ProjectMembership, Site
from apps.projects.services import (
    add_project_member,
    archive_project,
    create_site,
    create_project,
    delete_site,
    get_available_project_members,
    get_project_member_user_ids,
    get_project_members,
    remove_project_member,
    update_site,
    update_project,
    user_can_manage_project,
    user_can_view_project,
)

__all__ = [name for name in globals() if not name.startswith('__')]
