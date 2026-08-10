def build_generated_object_key(*, survey_id: int, file_id: int, filename: str) -> str:
    return f"surveys/{survey_id}/files/{file_id}/{filename}"


def build_generated_object_prefix(*, survey_id: int, file_id: int, prefix: str) -> str:
    return f"surveys/{survey_id}/files/{file_id}/{prefix}"


def build_map_tiles_prefix(*, survey_id: int, file_id: int) -> str:
    return build_generated_object_prefix(survey_id=survey_id, file_id=file_id, prefix="tiles")


def build_map_tile_key(*, survey_id: int, file_id: int, z: int, x: int, y: int) -> str:
    return f"{build_map_tiles_prefix(survey_id=survey_id, file_id=file_id)}/{z}/{x}/{y}.png"


def build_map_tile_metadata_key(*, survey_id: int, file_id: int) -> str:
    return f"{build_map_tiles_prefix(survey_id=survey_id, file_id=file_id)}/metadata.json"


def build_model_metadata_key(*, survey_id: int, file_id: int) -> str:
    return build_generated_object_key(survey_id=survey_id, file_id=file_id, filename="model-metadata.json")
