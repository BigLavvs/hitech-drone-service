from decimal import Decimal

from django.contrib.gis.geos import Point
from django.test import TestCase

from apps.access_control.models import User, UserRole
from apps.maps.models import Measurement, MeasurementType
from apps.projects.models import Project, Site
from apps.surveys.models import Survey


class MeasurementModelTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            email="creator@example.com",
            external_id="creator-ext-id",
            role=UserRole.SURVEY_ENGINEER,
        )
        self.project = Project.objects.create(
            name="Hitech Project",
            created_by=self.creator,
        )
        self.site = Site.objects.create(
            project=self.project,
            name="Primary Site",
            coordinates=Point(3.3792, 6.5244, srid=4326),
        )
        self.survey = Survey.objects.create(
            project=self.project,
            site=self.site,
            name="Baseline Survey",
            survey_date="2026-08-09",
            created_by=self.creator,
        )

    def test_measurement_types_and_fields_persist_correctly(self):
        distance_coordinates = [
            {"lat": 6.5244, "lng": 3.3792},
            {"lat": 6.5248, "lng": 3.3799},
        ]
        area_coordinates = [
            {"lat": 6.5244, "lng": 3.3792},
            {"lat": 6.5244, "lng": 3.3801},
            {"lat": 6.5251, "lng": 3.3801},
        ]

        distance = Measurement.objects.create(
            survey=self.survey,
            type=MeasurementType.DISTANCE,
            name="Road Offset",
            coordinates=distance_coordinates,
            calculated_value=Decimal("123.45678901"),
            unit="meters",
            created_by=self.creator,
        )
        area = Measurement.objects.create(
            survey=self.survey,
            type=MeasurementType.AREA,
            name="Stockpile Footprint",
            coordinates=area_coordinates,
            calculated_value=Decimal("99999.12345678"),
            unit="square_meters",
            created_by=self.creator,
        )

        distance.refresh_from_db()
        area.refresh_from_db()

        self.assertEqual(distance.type, MeasurementType.DISTANCE)
        self.assertEqual(area.type, MeasurementType.AREA)
        self.assertEqual(distance.coordinates, distance_coordinates)
        self.assertEqual(area.coordinates, area_coordinates)
        self.assertEqual(distance.calculated_value, Decimal("123.45678901"))
        self.assertEqual(area.calculated_value, Decimal("99999.12345678"))
        self.assertEqual(distance.unit, "meters")
        self.assertEqual(area.unit, "square_meters")

    def test_related_names_resolve_survey_and_creator_relationships(self):
        measurement = Measurement.objects.create(
            survey=self.survey,
            type=MeasurementType.DISTANCE,
            name="Fence Line",
            coordinates=[{"lat": 6.5, "lng": 3.3}, {"lat": 6.6, "lng": 3.4}],
            calculated_value=Decimal("10.00000000"),
            unit="meters",
            created_by=self.creator,
        )

        self.assertEqual(self.survey.measurements.get(), measurement)
        self.assertEqual(self.creator.measurements_created.get(), measurement)

    def test_deleting_survey_cascades_measurements(self):
        measurement = Measurement.objects.create(
            survey=self.survey,
            type=MeasurementType.AREA,
            name="Pad Area",
            coordinates=[{"lat": 6.5, "lng": 3.3}, {"lat": 6.6, "lng": 3.4}],
            calculated_value=Decimal("20.00000000"),
            unit="square_meters",
            created_by=self.creator,
        )

        survey_id = self.survey.id
        self.survey.delete()

        self.assertFalse(Measurement.objects.filter(pk=measurement.pk).exists())
        self.assertFalse(Survey.objects.filter(pk=survey_id).exists())

    def test_deleting_creator_sets_created_by_to_null(self):
        measurement = Measurement.objects.create(
            survey=self.survey,
            type=MeasurementType.DISTANCE,
            name="Rail Segment",
            coordinates=[{"lat": 6.5, "lng": 3.3}, {"lat": 6.6, "lng": 3.4}],
            calculated_value=Decimal("30.00000000"),
            unit="meters",
            created_by=self.creator,
        )

        self.creator.delete()
        measurement.refresh_from_db()

        self.assertIsNone(measurement.created_by)

    def test_decimal_precision_matches_documented_schema(self):
        field = Measurement._meta.get_field("calculated_value")

        self.assertEqual(field.max_digits, 20)
        self.assertEqual(field.decimal_places, 8)
