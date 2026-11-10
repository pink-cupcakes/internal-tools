"""Check deployment failures without Docker, credentials, or extra packages."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "langfuse"
SPEC = importlib.util.spec_from_file_location("langfuse_check", ROOT / "check.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def configuration():
    values = CHECK.load_configuration(ROOT / "configuration.example")
    for key in CHECK.REQUIRED.findall((ROOT / "compose.yaml").read_text()):
        if not values.get(key):
            values[key] = "a" * 64
    values.update(
        CLICKHOUSE_HOST="service.us-east-1.aws.clickhouse.cloud",
        CLICKHOUSE_USER="langfuse",
        MINIO_ROOT_USER="langfuse",
    )
    return values


class DeploymentTests(unittest.TestCase):
    def test_contract(self):
        CHECK.validate_contract(json.loads((ROOT / "compose.yaml").read_text()))

    def test_template_requires_secrets(self):
        with self.assertRaisesRegex(ValueError, "missing required"):
            CHECK.validate_values(
                CHECK.load_configuration(ROOT / "configuration.example"),
                (ROOT / "compose.yaml").read_text(),
            )

    def test_valid_configuration_and_each_missing_value(self):
        text = (ROOT / "compose.yaml").read_text()
        values = configuration()
        CHECK.validate_values(values, text)
        for key in set(CHECK.REQUIRED.findall(text)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                CHECK.validate_values({**values, key: ""}, text)

    def test_bad_cloud_host_and_nonlocal_http(self):
        text = (ROOT / "compose.yaml").read_text()
        for key, value in [
            ("CLICKHOUSE_HOST", "localhost"),
            ("NEXTAUTH_URL", "http://example.com"),
        ]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                CHECK.validate_values({**configuration(), key: value}, text)

    def test_reject_unpinned_image_and_public_port(self):
        for key, value in [("image", "langfuse:latest"), ("ports", ["3000:3000"])]:
            doc = json.loads((ROOT / "compose.yaml").read_text())
            doc["services"]["web"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                CHECK.validate_contract(doc)

    def test_bootstrap_precedes_apps(self):
        services = json.loads((ROOT / "compose.yaml").read_text())["services"]
        self.assertNotIn("depends_on", services["minio"])
        self.assertEqual(
            services["bucket-init"]["depends_on"]["minio"]["condition"],
            "service_healthy",
        )
        self.assertIn("mc mb --ignore-existing", services["bucket-init"]["command"][0])
        for name in ("web", "worker"):
            self.assertEqual(
                services[name]["depends_on"]["bucket-init"]["condition"],
                "service_completed_successfully",
            )
