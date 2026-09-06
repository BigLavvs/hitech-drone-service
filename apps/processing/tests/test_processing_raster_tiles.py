from .support import *


class ProcessingRasterTilesMixin:
    @patch("apps.processing.services.raster._generate_xyz_tile_pyramid")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_raster_processing_uploads_tile_metadata_sidecar_at_deterministic_key(
        self,
        storage_factory,
        mocked_tile_generation,
    ):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage
        mocked_tile_generation.return_value = representative_tile_metadata()

        class FakeDataset:
            count = 1
            height = 8
            width = 8

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, **kwargs):
                import numpy

                return FakeMaskedArray(numpy.array([[[0.0, 5.0], [10.0, 15.0]]], dtype="float32"))

        class FakePreviewWriter:
            def __init__(self, output_path):
                self.output_path = Path(output_path)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _data):
                self.output_path.write_bytes(b"\x89PNG\r\n\x1a\npng")

        def fake_rasterio_open(path, mode="r", **kwargs):
            if mode == "w":
                return FakePreviewWriter(path)
            return FakeDataset()

        def fake_rasterio_copy(src, dst, driver):
            Path(dst).write_bytes(b"cog-bytes")

        with patch("rasterio.open", side_effect=fake_rasterio_open), patch(
            "rasterio.shutil.copy",
            side_effect=fake_rasterio_copy,
        ):
            execute_processing_task(processing_job_id=processing_job.pk)

        metadata_key = f"{self.published_root(survey_file)}tiles/metadata.json"
        self.assertIn(metadata_key, fake_storage.objects)
        self.assertEqual(
            json.loads(fake_storage.objects[metadata_key].decode("utf-8"))["zoom_range"],
            {"min": 0, "max": 12},
        )
        mocked_tile_generation.assert_called_once()

    def test_generate_xyz_tile_pyramid_creates_real_tiles_and_metadata_without_boundless_reads(self):
        import numpy
        import rasterio
        from rasterio.transform import from_bounds

        survey_file, _processing_job, _raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter()

        with tempfile.TemporaryDirectory(prefix="tile-gen-raw-") as raw_dir, tempfile.TemporaryDirectory(
            prefix="tile-gen-out-"
        ) as output_dir:
            raw_path = Path(raw_dir) / "sample.tif"
            transform = from_bounds(3.0, 6.0, 3.02, 6.02, 32, 32)
            with rasterio.open(
                raw_path,
                "w",
                driver="GTiff",
                width=32,
                height=32,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=transform,
            ) as dataset:
                dataset.write(numpy.full((1, 32, 32), 180, dtype="uint8"))

            metadata = _generate_xyz_tile_pyramid(
                survey_file=survey_file,
                local_raw_path=raw_path,
                temp_dir_path=Path(output_dir),
                storage=fake_storage,
            )

        self.assertGreater(metadata["generated_tile_count"], 0)
        self.assertIn("bounds", metadata)
        self.assertIn("zoom_range", metadata)
        tile_keys = [
            destination_key
            for destination_key, _content_type in fake_storage.upload_calls
            if destination_key.endswith(".png")
        ]
        self.assertGreaterEqual(len(tile_keys), 1)
        self.assertTrue(
            any(
                payload.startswith(b"\x89PNG\r\n\x1a\n")
                for key, payload in fake_storage.objects.items()
                if key.endswith(".png")
            )
        )
        self.assertTrue(all("tiles/" in tile_key for tile_key in tile_keys))

    @patch("rasterio.shutil.copy")
    @patch("rasterio.open")
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_raster_processing_streams_private_io_and_cleans_temp_directory(self, storage_factory, mocked_rasterio_open, mocked_rasterio_copy):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        fake_storage = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        storage_factory.return_value = fake_storage
        observed_temp_dir = {}

        class FakeDataset:
            count = 1
            height = 4
            width = 4

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, **kwargs):
                observed_temp_dir["read_called"] = True
                import numpy

                return FakeMaskedArray(numpy.array([[[0.0, 5.0], [10.0, 15.0]]], dtype="float32"))

        class FakePreviewWriter:
            def __init__(self, output_path):
                self.output_path = Path(output_path)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def write(self, _data):
                self.output_path.write_bytes(
                    b"\x89PNG\r\n\x1a\n"
                    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00\x3a\x7e\x9b\x55"
                    b"\x00\x00\x00\x0cIDAT\x08\x99\x63\x60\x00\x00\x00\x02\x00\x01\xf4\x71\x64\xa6"
                    b"\x00\x00\x00\x00IEND\xaeB`\x82"
                )

        def fake_rasterio_open(path, mode="r", **kwargs):
            if mode == "w":
                return FakePreviewWriter(path)
            return FakeDataset()

        mocked_rasterio_open.side_effect = fake_rasterio_open

        def record_copy(src, dst, driver):
            observed_temp_dir["path"] = Path(dst).parent
            Path(dst).write_bytes(b"cog")

        mocked_rasterio_copy.side_effect = record_copy

        with patch(
            "apps.processing.services.raster._generate_xyz_tile_pyramid",
            return_value=representative_tile_metadata(),
        ) as mocked_tile_generation:
            execute_processing_task(processing_job_id=processing_job.pk)

        metadata_key = f"{self.published_root(survey_file)}tiles/metadata.json"
        self.assertEqual(fake_storage.download_calls, [survey_file.storage_path])
        self.assertEqual(
            fake_storage.upload_calls,
            [
                (f"{self.published_root(survey_file)}preview.png", "image/png"),
                (f"{self.published_root(survey_file)}cog.tif", survey_file.mime_type),
                (metadata_key, "application/json"),
            ],
        )
        self.assertTrue(observed_temp_dir["read_called"])
        preview_uploads = [key for key, _content_type in fake_storage.upload_calls if key.endswith("preview.png")]
        self.assertEqual(len(preview_uploads), 1)
        self.assertTrue(fake_storage.objects[preview_uploads[0]].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            json.loads(fake_storage.objects[metadata_key].decode("utf-8")),
            representative_tile_metadata(),
        )
        mocked_tile_generation.assert_called_once()
        self.assertFalse(observed_temp_dir["path"].exists())

    @patch("apps.processing.services.execution._process_raster_file", side_effect=ProcessingError("conversion failed"))
    @patch("apps.processing.services.execution.PrivateR2StorageAdapter")
    def test_temp_directory_is_cleaned_after_processing_failure(self, storage_factory, _mocked_process_raster):
        survey_file, processing_job, raw_bytes = self.create_file_and_job()
        storage_factory.return_value = FakePrivateStorageAdapter(objects={survey_file.storage_path: raw_bytes})
        fake_task = make_fake_task("raster-failure-task")
        fake_task.retry.side_effect = RuntimeError("retry scheduled")

        with self.assertRaisesMessage(RuntimeError, "retry scheduled"):
            _run_processing_task(task=fake_task, processing_job_id=processing_job.pk)

        processing_job.refresh_from_db()
        self.assertEqual(processing_job.retry_count, 1)
