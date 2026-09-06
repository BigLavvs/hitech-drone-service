import re

from django.core.exceptions import ValidationError


def generated_object_root(*, survey_id: int, file_id: int, published_path: str | None = None) -> str:
    root = f"surveys/{survey_id}/files/{file_id}/"
    if published_path and published_path.startswith(root + "attempts/"):
        suffix = published_path[len(root):]
        match = re.match(r"attempts/([0-9a-f]{32})/[^/]+", suffix)
        if match is None or any(part in {"", ".", ".."} for part in suffix.split("/")):
            raise ValidationError("Invalid published processing path.")
        return f"{root}attempts/{match.group(1)}/"
    if published_path and "/attempts/" in published_path:
        raise ValidationError("Published processing path belongs to another file.")
    # Older records may have custom preview/conversion paths. Their sidecars
    # still live at the original deterministic file root.
    return root


def build_generated_object_key(*, survey_id: int, file_id: int, filename: str, published_path=None) -> str:
    return generated_object_root(survey_id=survey_id, file_id=file_id, published_path=published_path) + filename


def build_generated_object_prefix(*, survey_id: int, file_id: int, prefix: str) -> str:
    return f"surveys/{survey_id}/files/{file_id}/{prefix}"


def build_map_tiles_prefix(*, survey_id: int, file_id: int, published_path=None) -> str:
    return build_generated_object_key(survey_id=survey_id, file_id=file_id, filename="tiles", published_path=published_path)


def build_map_tile_key(*, survey_id: int, file_id: int, z: int, x: int, y: int, published_path=None) -> str:
    return f"{build_map_tiles_prefix(survey_id=survey_id, file_id=file_id, published_path=published_path)}/{z}/{x}/{y}.png"


def build_map_tile_metadata_key(*, survey_id: int, file_id: int, published_path=None) -> str:
    return f"{build_map_tiles_prefix(survey_id=survey_id, file_id=file_id, published_path=published_path)}/metadata.json"


def build_model_metadata_key(*, survey_id: int, file_id: int, published_path=None) -> str:
    return build_generated_object_key(survey_id=survey_id, file_id=file_id, filename="model-metadata.json", published_path=published_path)
