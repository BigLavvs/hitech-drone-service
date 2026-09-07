from .support import *


class UploadAdmissionAssetsMixin:
    def test_obj_assets_are_persisted_and_count_toward_survey_total(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nmtllib materials.mtl\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        asset_upload = TrackingUpload(
            name="materials.mtl",
            content=b"newmtl material\nmap_Kd texture.png\n",
            content_type="text/plain",
        )

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=obj_upload,
            asset_files=[asset_upload],
            storage=storage,
        )

        asset = SurveyFileAsset.objects.get(survey_file=result.survey_file)
        self.assertEqual(asset.original_filename, "materials.mtl")
        self.assertEqual(asset.storage_path, f"surveys/{self.survey.pk}/files/{result.survey_file.pk}/assets/materials.mtl")
        self.assertIn(asset.storage_path, storage.objects)

    def test_gltf_external_assets_must_match_manifest_and_are_persisted(self):
        storage = FakePrivateStorageAdapter()
        gltf_upload = self.make_upload(
            name="scene.gltf",
            content=(
                b'{"asset":{"version":"2.0"},"buffers":[{"uri":"scene.bin"}],'
                b'"images":[{"uri":"textures/albedo.jpeg"}]}'
            ),
            content_type="model/gltf+json",
        )
        binary_asset = TrackingUpload(
            name="scene.bin",
            content=b"binary-buffer",
            content_type="application/octet-stream",
        )
        texture_asset = TrackingUpload(
            name="albedo.jpeg",
            content=b"\xff\xd8\xfftexture",
            content_type="image/jpeg",
        )

        result = admit_uploaded_file(
            actor=self.admin,
            survey=self.survey,
            uploaded_file=gltf_upload,
            asset_files=[binary_asset, texture_asset],
            storage=storage,
        )

        self.assertEqual(
            set(
                SurveyFileAsset.objects.filter(survey_file=result.survey_file).values_list(
                    "stored_filename", flat=True
                )
            ),
            {"scene.bin", "albedo.jpeg"},
        )

    def test_gltf_rejects_missing_or_unreferenced_assets_before_storage(self):
        storage = FakePrivateStorageAdapter()
        for asset_files in (
            [],
            [
                TrackingUpload(
                    name="scene.bin",
                    content=b"binary-buffer",
                    content_type="application/octet-stream",
                ),
                TrackingUpload(
                    name="unreferenced.png",
                    content=valid_png_bytes(),
                    content_type="image/png",
                ),
            ],
        ):
            with self.subTest(asset_count=len(asset_files)):
                gltf_upload = self.make_upload(
                    name="scene.gltf",
                    content=b'{"asset":{"version":"2.0"},"buffers":[{"uri":"scene.bin"}]}',
                    content_type="model/gltf+json",
                )
                with self.assertRaisesMessage(
                    ValidationError,
                    "GLTF related assets must exactly match the external buffers and images referenced by the GLTF manifest.",
                ):
                    admit_uploaded_file(
                        actor=self.admin,
                        survey=self.survey,
                        uploaded_file=gltf_upload,
                        asset_files=asset_files,
                        storage=storage,
                    )

        self.assertEqual(storage.uploaded, [])
        self.assertEqual(SurveyFile.objects.count(), 0)

    def test_invalid_obj_asset_is_rejected_before_storage(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        invalid_asset = TrackingUpload(
            name="payload.gif",
            content=b"GIF89a",
            content_type="image/gif",
        )

        with self.assertRaisesMessage(ValidationError, "Unsupported OBJ asset extension."):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[invalid_asset],
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])
        self.assertEqual(SurveyFileAsset.objects.count(), 0)

    def test_malformed_obj_mtl_asset_is_rejected_before_storage_even_with_browser_generic_mime(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        invalid_asset = TrackingUpload(
            name="materials.mtl",
            content=b"not valid mtl content",
            content_type="application/octet-stream",
        )

        with self.assertRaisesMessage(ValidationError, "MTL content is malformed."):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[invalid_asset],
                storage=storage,
            )

        self.assertEqual(storage.uploaded, [])
        self.assertEqual(SurveyFileAsset.objects.count(), 0)

    def test_asset_promotion_failure_rolls_back_primary_file_and_assets(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nmtllib materials.mtl\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        asset_upload = TrackingUpload(
            name="materials.mtl",
            content=b"newmtl material\n",
            content_type="text/plain",
        )
        original_promote = storage.promote_object

        def fail_asset_promotion(*, source_key, destination_key, content_type):
            if "/assets/" in destination_key:
                storage.objects[destination_key] = {"content": b"asset", "content_type": content_type}
                raise RuntimeError("asset promotion failed")
            return original_promote(
                source_key=source_key,
                destination_key=destination_key,
                content_type=content_type,
            )

        storage.promote_object = fail_asset_promotion

        with self.assertRaisesMessage(RuntimeError, "asset promotion failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[asset_upload],
                storage=storage,
            )

        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(SurveyFileAsset.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(storage.objects, {})

    def test_later_asset_staging_failure_cleans_up_primary_and_earlier_assets(self):
        storage = FakePrivateStorageAdapter()
        obj_upload = self.make_upload(
            name="mesh.obj",
            content=b"# mesh\nmtllib materials.mtl\nv 0.0 0.0 0.0\nf 1 1 1\n",
            content_type="model/obj",
        )
        first_asset = TrackingUpload(
            name="materials.mtl",
            content=b"newmtl material\n",
            content_type="text/plain",
        )
        second_asset = TrackingUpload(
            name="texture.png",
            content=valid_png_bytes(),
            content_type="image/png",
        )
        original_upload_to_staging = storage.upload_to_staging

        def fail_later_asset_staging(*, survey_id, filename, file_obj, content_type, identifier=None):
            if filename == "texture.png":
                raise RuntimeError("later asset staging failed")
            return original_upload_to_staging(
                survey_id=survey_id,
                filename=filename,
                file_obj=file_obj,
                content_type=content_type,
                identifier=identifier,
            )

        storage.upload_to_staging = fail_later_asset_staging

        with self.assertRaisesMessage(RuntimeError, "later asset staging failed"):
            admit_uploaded_file(
                actor=self.admin,
                survey=self.survey,
                uploaded_file=obj_upload,
                asset_files=[first_asset, second_asset],
                storage=storage,
            )

        self.assertEqual(storage.objects, {})
        self.assertEqual(SurveyFile.objects.count(), 0)
        self.assertEqual(SurveyFileAsset.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), 0)
