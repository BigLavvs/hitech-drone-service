from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator


class OpenAPIContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def test_upload_contract_describes_multipart_request_and_async_response(self):
        operation = self.schema["paths"]["/api/v1/surveys/{survey_id}/files"]["post"]

        self.assertEqual(
            operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"],
            "#/components/schemas/SurveyFileUploadRequest",
        )
        upload_schema = self.schema["components"]["schemas"]["SurveyFileUploadRequest"]
        self.assertEqual(upload_schema["properties"]["file"]["format"], "binary")
        self.assertEqual(upload_schema["properties"]["assets"]["items"]["format"], "binary")
        self.assertIn("202", operation["responses"])
        self.assertNotIn("SurveyFileUploadResponse", operation["requestBody"]["content"]["multipart/form-data"]["schema"].get("$ref", ""))

    def test_collection_and_nested_file_contracts_match_runtime_shapes(self):
        file_list = self.schema["paths"]["/api/v1/surveys/{survey_id}/files"]["get"]
        self.assertEqual(file_list["responses"]["200"]["content"]["application/json"]["schema"]["type"], "array")

        processing_file = self.schema["components"]["schemas"]["ProcessingJobDetail"]["properties"]["file"]
        self.assertEqual(processing_file["allOf"][0]["$ref"], "#/components/schemas/SurveyFileListItem")

        projects_list = self.schema["paths"]["/api/v1/projects"]["get"]
        self.assertEqual(
            projects_list["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/PaginatedProjectRead",
        )

    def test_operation_ids_are_unique_and_role_enum_is_named(self):
        operation_ids = [
            operation["operationId"]
            for path in self.schema["paths"].values()
            for operation in path.values()
            if isinstance(operation, dict) and "operationId" in operation
        ]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        self.assertIn("UserRole", self.schema["components"]["schemas"])
        self.assertNotIn("Role909Enum", self.schema["components"]["schemas"])
