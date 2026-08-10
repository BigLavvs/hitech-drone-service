from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models


class UserRole(models.TextChoices):
    ADMINISTRATOR = "ADMINISTRATOR", "Administrator"
    PROJECT_MANAGER = "PROJECT_MANAGER", "Project Manager"
    SURVEY_ENGINEER = "SURVEY_ENGINEER", "Survey Engineer"
    VIEWER = "VIEWER", "Viewer"


class UserManager(BaseUserManager):
    def create_user(self, email, external_id, role, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        if not external_id:
            raise ValueError("External ID is required")

        email = self.normalize_email(email)
        user = self.model(email=email, external_id=external_id, role=role, **extra_fields)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, external_id, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(
            email=email,
            external_id=external_id,
            role=UserRole.ADMINISTRATOR,
            **extra_fields,
        )


class User(AbstractBaseUser):
    external_id = models.CharField(max_length=255, unique=True, db_index=True)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=50, choices=UserRole.choices)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["external_id"]

    objects = UserManager()

    class Meta:
        db_table = "user"
        indexes = [
            models.Index(fields=["role"]),
        ]

    def __str__(self):
        return f"{self.email} ({self.role})"
