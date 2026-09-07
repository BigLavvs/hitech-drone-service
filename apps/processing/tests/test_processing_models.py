from .support import *
from apps.files.tests.support import valid_jpeg_bytes, valid_png_bytes


class ProcessingModelProcessingMixin:
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_browser_ready_formats_skip_redundant_conversion(self, storage_factory):
        for file_format, payload in (
            (FileFormat.PNG, valid_png_bytes(interlaced=True)),
            (FileFormat.JPEG, valid_jpeg_bytes()),
            (FileFormat.KML, b'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>'),
            (FileFormat.GEOJSON, b'{"type":"FeatureCollection","features":[]}'),
        ):
            with self.subTest(file_format=file_format):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Survey {file_format}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename=f"source.{file_format.lower()}",
                    stored_filename=f"source-{file_format}.{file_format.lower()}",
                    file_type=FileType.TWO_D if file_format in {FileFormat.PNG, FileFormat.JPEG, FileFormat.KML, FileFormat.GEOJSON} else FileType.THREE_D,
                    format=file_format,
                    mime_type={
                        FileFormat.PNG: "image/png",
                        FileFormat.JPEG: "image/jpeg",
                        FileFormat.KML: "application/vnd.google-earth.kml+xml",
                        FileFormat.GEOJSON: "application/geo+json",
                        FileFormat.GLB: "model/gltf-binary",
                        FileFormat.GLTF: "model/gltf+json",
                    }[file_format],
                    size_bytes=len(payload),
                    sha256_checksum=__import__("hashlib").sha256(payload).hexdigest(),
                    storage_path=f"browser-ready-{file_format}",
                    uploaded_by=self.engineer,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: payload})

                execute_processing_task(processing_job_id=processing_job.pk)

                survey_file.refresh_from_db()
                self.assertIsNone(survey_file.preview_path)
                self.assertIsNone(survey_file.converted_path)

    @patch("apps.processing.services.model_processing._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_mesh_formats_keep_full_conversion_and_store_reduced_preview(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        for file_format, raw_bytes, mime_type in (
            (FileFormat.OBJ, b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n", "model/obj"),
            (
                FileFormat.PLY,
                b"ply\nformat ascii 1.0\nelement vertex 1\n"
                b"property float x\nproperty float y\nproperty float z\n"
                b"end_header\n0 0 0\n",
                "application/ply",
            ),
            (FileFormat.STL, b"solid mesh\nfacet normal 0 0 1\nendfacet\nendsolid mesh\n", "model/stl"),
        ):
            with self.subTest(file_format=file_format):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Mesh Survey {file_format}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename=f"mesh.{file_format.lower()}",
                    stored_filename=f"mesh.{file_format.lower()}",
                    file_type=FileType.THREE_D,
                    format=file_format,
                    mime_type=mime_type,
                    size_bytes=len(raw_bytes),
                    sha256_checksum=__import__("hashlib").sha256(raw_bytes).hexdigest(),
                    storage_path=f"mesh-{file_format.lower()}",
                    uploaded_by=self.engineer,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
                storage_factory.return_value = fake_storage

                class FakeMesh:
                    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

                    def export(self, output_path, file_type):
                        Path(output_path).write_bytes(f"{file_type}-full".encode("utf-8"))

                def fake_preview_export(*, mesh, destination_path):
                    destination_path.write_bytes(b"preview-glb")

                mocked_trimesh_load.return_value = FakeMesh()
                mocked_export_preview.side_effect = fake_preview_export

                execute_processing_task(processing_job_id=processing_job.pk)

                survey_file.refresh_from_db()
                self.assertEqual(
                    survey_file.preview_path,
                    f"{self.published_root(survey_file)}preview.glb",
                )
                self.assertEqual(
                    survey_file.converted_path,
                    f"{self.published_root(survey_file)}model.glb",
                )
                self.assertEqual(fake_storage.objects[survey_file.preview_path], b"preview-glb")
                self.assertEqual(fake_storage.objects[survey_file.converted_path], b"glb-full")

    @patch("apps.processing.services.model_processing._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_model_processing_uploads_private_metadata_sidecar(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        raw_bytes = b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n"
        survey_file, processing_job, _raw = self.create_file_and_job(
            file_format=FileFormat.OBJ,
            content=raw_bytes,
        )
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage

        class FakeBounds:
            def tolist(self):
                return [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]]

        class FakeVertices:
            def __len__(self):
                return 6

        class FakeMesh:
            faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]
            vertices = FakeVertices()
            bounds = FakeBounds()
            metadata = {"crs": "EPSG:4978"}

            def export(self, output_path, file_type):
                Path(output_path).write_bytes(b"glb-output")

            def copy(self):
                return self

        mocked_trimesh_load.return_value = FakeMesh()
        mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
            b"preview-glb"
        )

        execute_processing_task(processing_job_id=processing_job.pk)

        metadata_key = f"{self.published_root(survey_file)}model-metadata.json"
        self.assertIn(metadata_key, fake_storage.objects)
        payload = json.loads(fake_storage.objects[metadata_key].decode("utf-8"))
        self.assertEqual(payload["display_format"], "GLB")
        self.assertEqual(payload["vertex_count"], 6)
        self.assertEqual(payload["bounding_box"]["min"], [0.0, 1.0, 2.0])
        self.assertEqual(payload["crs"], "EPSG:4978")

    @patch("apps.processing.services.model_processing._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_glb_and_gltf_keep_raw_source_and_store_preview_proxy(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        for file_format, payload, mime_type in (
            (FileFormat.GLB, b"glTF\x02\x00\x00\x00rest", "model/gltf-binary"),
            (FileFormat.GLTF, b'{"asset":{"version":"2.0"},"scenes":[{"nodes":[]}]}', "model/gltf+json"),
        ):
            with self.subTest(file_format=file_format):
                survey = Survey.objects.create(
                    project=self.project,
                    site=self.site,
                    name=f"Browser Ready Model {file_format}",
                    survey_date=date(2026, 8, 9),
                    created_by=self.engineer,
                )
                survey_file = SurveyFile.objects.create(
                    survey=survey,
                    original_filename=f"scene.{file_format.lower()}",
                    stored_filename=f"scene.{file_format.lower()}",
                    file_type=FileType.THREE_D,
                    format=file_format,
                    mime_type=mime_type,
                    size_bytes=len(payload),
                    sha256_checksum=__import__("hashlib").sha256(payload).hexdigest(),
                    storage_path=f"browser-ready-{file_format.lower()}",
                    uploaded_by=self.engineer,
                )
                processing_job = create_queued_processing_job(survey_file=survey_file)
                fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: payload})
                storage_factory.return_value = fake_storage

                class FakeMesh:
                    faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

                mocked_trimesh_load.return_value = FakeMesh()
                mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
                    b"proxy-glb"
                )

                execute_processing_task(processing_job_id=processing_job.pk)

                survey_file.refresh_from_db()
                self.assertEqual(
                    survey_file.preview_path,
                    f"{self.published_root(survey_file)}preview.glb",
                )
                self.assertIsNone(survey_file.converted_path)
                self.assertEqual(fake_storage.objects[survey_file.preview_path], b"proxy-glb")

    @patch("apps.processing.services.model_processing._export_reduced_glb_preview")
    @patch("trimesh.load")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_external_gltf_assets_are_staged_and_converted_to_glb(
        self,
        storage_factory,
        mocked_trimesh_load,
        mocked_export_preview,
    ):
        gltf_payload = (
            b'{"asset":{"version":"2.0"},"buffers":[{"uri":"scene.bin"}],'
            b'"images":[{"uri":"textures/albedo.jpeg"}]}'
        )
        survey_file, processing_job, _ = self.create_file_and_job(
            file_format=FileFormat.GLTF,
            content=gltf_payload,
        )
        binary_payload = b"external-buffer"
        texture_payload = b"\xff\xd8\xfftexture"
        binary_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/assets/scene.bin"
        texture_key = f"surveys/{self.survey.pk}/files/{survey_file.pk}/assets/albedo.jpeg"
        SurveyFileAsset.objects.create(
            survey_file=survey_file,
            original_filename="scene.bin",
            stored_filename="scene.bin",
            mime_type="application/octet-stream",
            size_bytes=len(binary_payload),
            sha256_checksum=__import__("hashlib").sha256(binary_payload).hexdigest(),
            storage_path=binary_key,
        )
        SurveyFileAsset.objects.create(
            survey_file=survey_file,
            original_filename="albedo.jpeg",
            stored_filename="albedo.jpeg",
            mime_type="image/jpeg",
            size_bytes=len(texture_payload),
            sha256_checksum=__import__("hashlib").sha256(texture_payload).hexdigest(),
            storage_path=texture_key,
        )
        fake_storage = FakePrivateStorageAdapter(
            objects={
                survey_file.storage_path: gltf_payload,
                binary_key: binary_payload,
                texture_key: texture_payload,
            }
        )
        storage_factory.return_value = fake_storage

        class FakeMesh:
            faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

            def export(self, output_path, file_type):
                Path(output_path).write_bytes(f"{file_type}-full".encode("utf-8"))

        mocked_trimesh_load.return_value = FakeMesh()
        mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
            b"preview-glb"
        )

        execute_processing_task(processing_job_id=processing_job.pk)

        survey_file.refresh_from_db()
        self.assertEqual(
            survey_file.converted_path,
            f"{self.published_root(survey_file)}model.glb",
        )
        self.assertEqual(fake_storage.objects[survey_file.converted_path], b"glb-full")
        self.assertEqual(fake_storage.download_calls[:3], [survey_file.storage_path, binary_key, texture_key])

    @patch("trimesh.load")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_obj_conversion_downloads_related_assets_and_uploads_glb(self, storage_factory, mocked_trimesh_load):
        raw_bytes = b"# test\nv 0.0 0.0 0.0\nf 1 1 1\n"
        survey_file, processing_job, _raw = self.create_file_and_job(file_format=FileFormat.OBJ, content=raw_bytes)
        asset_bytes = b"newmtl material\n"
        SurveyFileAsset.objects.create(
            survey_file=survey_file,
            original_filename="materials.mtl",
            stored_filename="materials.mtl",
            mime_type="text/plain",
            size_bytes=len(asset_bytes),
            sha256_checksum=__import__("hashlib").sha256(asset_bytes).hexdigest(),
            storage_path="obj-asset",
        )
        fake_storage = FakePrivateStorageAdapter(
            objects={survey_file.storage_path: raw_bytes, "obj-asset": asset_bytes}
        )
        storage_factory.return_value = fake_storage

        exported_paths = []

        class FakeMesh:
            faces = [(0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5)]

            def export(self, output_path, file_type):
                exported_paths.append((output_path, file_type))
                Path(output_path).write_bytes(b"glb-output")

            def copy(self):
                return self

        mocked_trimesh_load.return_value = FakeMesh()

        with patch("apps.processing.services.model_processing._export_reduced_glb_preview") as mocked_export_preview:
            mocked_export_preview.side_effect = lambda *, mesh, destination_path: destination_path.write_bytes(
                b"preview-glb"
            )
            execute_processing_task(processing_job_id=processing_job.pk)

        metadata_key = f"{self.published_root(survey_file)}model-metadata.json"
        preview_key = f"{self.published_root(survey_file)}preview.glb"
        converted_key = f"{self.published_root(survey_file)}model.glb"
        self.assertEqual(fake_storage.download_calls, [survey_file.storage_path, "obj-asset"])
        self.assertEqual(
            fake_storage.upload_calls,
            [
                (metadata_key, "application/json"),
                (preview_key, "model/gltf-binary"),
                (converted_key, "model/gltf-binary"),
            ],
        )
        self.assertEqual(fake_storage.objects[preview_key], b"preview-glb")
        self.assertEqual(fake_storage.objects[converted_key], b"glb-output")
        self.assertEqual(
            json.loads(fake_storage.objects[metadata_key].decode("utf-8"))["display_format"],
            "GLB",
        )
        self.assertEqual(exported_paths[0][1], "glb")
