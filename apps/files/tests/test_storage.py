from .support import *


class PrivateR2StorageAdapterTests(TestCase):
    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_r2_client_uses_fake_overridden_settings_only(self, client_factory):
        client = Mock()
        client_factory.return_value = client

        adapter = PrivateR2StorageAdapter()

        client_factory.assert_called_once_with(
            "s3",
            endpoint_url="https://example.invalid",
            aws_access_key_id="test-access-key",
            aws_secret_access_key="test-secret-key",
            region_name="auto",
        )
        self.assertEqual(adapter.bucket_name, "test-bucket")

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_managed_multipart_upload_uses_chunk_settings_and_no_public_acl(self, client_factory):
        client = Mock()
        client_factory.return_value = client

        adapter = PrivateR2StorageAdapter()
        upload = TrackingUpload(
            name="North Block Final.glb",
            content=b"glTF\x02\x00\x00\x00rest",
            content_type="model/gltf-binary",
        )
        upload.seek(4)

        storage_key = adapter.upload(
            survey_id=42,
            filename=upload.name,
            file_obj=upload,
            content_type=upload.content_type,
            identifier="fixed123",
        )

        client.upload_fileobj.assert_called_once()

        kwargs = client.upload_fileobj.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(
            kwargs["Key"],
            "surveys/42/staging/fixed123_North-Block-Final.glb",
        )
        self.assertIs(kwargs["Fileobj"]._file_obj, upload)
        self.assertEqual(kwargs["ExtraArgs"]["ContentType"], "model/gltf-binary")
        self.assertNotIn("ACL", kwargs["ExtraArgs"])
        self.assertEqual(
            kwargs["Config"].multipart_threshold,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(
            kwargs["Config"].multipart_chunksize,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )

        self.assertEqual(upload.tell(), 0)
        self.assertEqual(
            storage_key,
            "surveys/42/staging/fixed123_North-Block-Final.glb",
        )
        client.put_object.assert_not_called()

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_upload_to_staging_streams_small_reads_and_returns_sha256_checksum(self, client_factory):
        client = Mock()
        client_factory.return_value = client
        original_bytes = b"glTF\x02\x00\x00\x00payload-for-sha256"
        upload = TrackingUpload(
            name="North Block Final.glb",
            content=original_bytes,
            content_type="model/gltf-binary",
        )
        upload.seek(7)
        read_sizes = []

        def consume_fileobj(*, Fileobj, Bucket, Key, ExtraArgs, Config):
            self.assertEqual(Bucket, "test-bucket")
            self.assertEqual(
                Key,
                "surveys/42/staging/fixed123_North-Block-Final.glb",
            )
            self.assertEqual(ExtraArgs, {"ContentType": "model/gltf-binary"})
            self.assertNotIn("ACL", ExtraArgs)
            self.assertEqual(
                Config.multipart_threshold,
                FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
            )
            self.assertEqual(
                Config.multipart_chunksize,
                FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
            )

            chunks = []
            while True:
                chunk = Fileobj.read(3)
                read_sizes.append(3)
                if not chunk:
                    break
                chunks.append(chunk)

            self.assertEqual(b"".join(chunks), original_bytes)

        client.upload_fileobj.side_effect = consume_fileobj

        adapter = PrivateR2StorageAdapter()
        staged_upload = adapter.upload_to_staging(
            survey_id=42,
            filename=upload.name,
            file_obj=upload,
            content_type=upload.content_type,
            identifier="fixed123",
        )

        client.upload_fileobj.assert_called_once()
        client.put_object.assert_not_called()
        client.copy.assert_not_called()
        client.delete_object.assert_not_called()
        self.assertEqual(
            staged_upload.storage_key,
            "surveys/42/staging/fixed123_North-Block-Final.glb",
        )
        self.assertEqual(
            staged_upload.sha256_checksum,
            hashlib.sha256(original_bytes).hexdigest(),
        )
        self.assertEqual(upload.max_requested_read, 3)
        self.assertGreater(len(read_sizes), 1)
        self.assertEqual(upload.tell(), len(original_bytes))

    def test_storage_key_generation_is_server_controlled_and_sanitized(self):
        adapter = PrivateR2StorageAdapter(client=Mock(), bucket_name="private-bucket")

        key = adapter.build_storage_key(
            survey_id=7,
            filename="../Quarterly Survey (North).laz",
            identifier="abc123",
        )

        self.assertEqual(key, "surveys/7/staging/abc123_Quarterly-Survey-North.laz")

    def test_staging_and_canonical_key_generation_follow_documented_private_layout(self):
        adapter = PrivateR2StorageAdapter(client=Mock(), bucket_name="private-bucket")

        staging_key = adapter.build_staging_key(
            survey_id=7,
            filename="../Quarterly Survey (North).laz",
            identifier="abc123",
        )
        canonical_key = adapter.build_canonical_key(
            survey_id=7,
            file_id=99,
            extension=".laz",
        )

        self.assertEqual(
            staging_key,
            "surveys/7/staging/abc123_Quarterly-Survey-North.laz",
        )
        self.assertEqual(canonical_key, "surveys/7/files/99/raw.laz")

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_promotion_uses_server_side_copy_then_deletes_staging_without_public_url(self, client_factory):
        client = Mock()
        client_factory.return_value = client

        adapter = PrivateR2StorageAdapter()
        adapter.promote_object(
            source_key="surveys/7/staging/upload_Quarterly-Survey-North.laz",
            destination_key="surveys/7/files/99/raw.laz",
            content_type="application/vnd.laszip",
        )

        client.copy.assert_called_once()
        copy_kwargs = client.copy.call_args.kwargs
        self.assertEqual(copy_kwargs["Bucket"], "test-bucket")
        self.assertEqual(copy_kwargs["Key"], "surveys/7/files/99/raw.laz")
        self.assertEqual(
            copy_kwargs["CopySource"],
            {
                "Bucket": "test-bucket",
                "Key": "surveys/7/staging/upload_Quarterly-Survey-North.laz",
            },
        )
        self.assertEqual(
            copy_kwargs["ExtraArgs"],
            {
                "ContentType": "application/vnd.laszip",
                "MetadataDirective": "REPLACE",
            },
        )
        self.assertEqual(copy_kwargs["Config"].multipart_threshold, FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"])
        self.assertEqual(copy_kwargs["Config"].multipart_chunksize, FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"])
        client.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="surveys/7/staging/upload_Quarterly-Survey-North.laz",
        )
        client.put_object.assert_not_called()

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_worker_download_streams_private_object_to_writable_fileobj(self, client_factory):
        client = Mock()
        client_factory.return_value = client
        destination = BytesIO()

        adapter = PrivateR2StorageAdapter()
        adapter.download_to_fileobj(
            storage_key="surveys/7/files/99/raw.laz",
            file_obj=destination,
        )

        client.download_fileobj.assert_called_once()
        kwargs = client.download_fileobj.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(kwargs["Key"], "surveys/7/files/99/raw.laz")
        self.assertIs(kwargs["Fileobj"], destination)
        self.assertEqual(
            kwargs["Config"].multipart_threshold,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(
            kwargs["Config"].multipart_chunksize,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        client.put_object.assert_not_called()
        client.copy.assert_not_called()
        client.delete_object.assert_not_called()

    @override_settings(**FAKE_R2_SETTINGS)
    @patch("apps.files.storage.boto3.client")
    def test_worker_generated_output_upload_uses_managed_multipart_without_public_acl(self, client_factory):
        client = Mock()
        client_factory.return_value = client
        generated_output = TrackingUpload(
            name="mesh.glb",
            content=b"glTF\x02\x00\x00\x00generated-output",
            content_type="model/gltf-binary",
        )
        generated_output.seek(6)

        adapter = PrivateR2StorageAdapter()
        adapter.upload_generated_fileobj(
            destination_key="surveys/7/files/99/generated/mesh.glb",
            file_obj=generated_output,
            content_type="model/gltf-binary",
        )

        client.upload_fileobj.assert_called_once()
        kwargs = client.upload_fileobj.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "test-bucket")
        self.assertEqual(kwargs["Key"], "surveys/7/files/99/generated/mesh.glb")
        self.assertIs(kwargs["Fileobj"], generated_output)
        self.assertEqual(kwargs["ExtraArgs"], {"ContentType": "model/gltf-binary"})
        self.assertNotIn("ACL", kwargs["ExtraArgs"])
        self.assertEqual(
            kwargs["Config"].multipart_threshold,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(
            kwargs["Config"].multipart_chunksize,
            FAKE_R2_SETTINGS["UPLOAD_CHUNK_SIZE_BYTES"],
        )
        self.assertEqual(generated_output.tell(), 0)
        client.put_object.assert_not_called()
        client.copy.assert_not_called()
        client.delete_object.assert_not_called()
