import json
import unittest
from unittest.mock import AsyncMock

from app.services.team import TeamService
from app.utils.token_parser import TokenParser


class TeamImportJsonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parser = TokenParser()

    def test_parse_cpa_single_object_json(self):
        payload = {
            "id_token": "eyJidC5pZC50b2tlbg.sig.part",
            "access_token": "eyJhY2Nlc3MudG9rZW4.sig.part",
            "refresh_token": "rt_test.refresh-token",
            "account_id": "123e4567-e89b-12d3-a456-426614174000",
            "email": "cpa@example.com"
        }

        result = self.parser.parse_team_import_content(json.dumps(payload))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["token"], payload["access_token"])
        self.assertEqual(result[0]["refresh_token"], payload["refresh_token"])
        self.assertEqual(result[0]["account_id"], payload["account_id"])
        self.assertEqual(result[0]["email"], payload["email"])

    def test_parse_cpa_object_array_json(self):
        payload = [
            {
                "access_token": "eyJmaXJzdC50b2tlbg.sig.part",
                "account_id": "123e4567-e89b-12d3-a456-426614174000",
                "email": "first@example.com"
            },
            {
                "access_token": "eyJzZWNvbmQudG9rZW4.sig.part",
                "account_id": "123e4567-e89b-12d3-a456-426614174001",
                "email": "second@example.com"
            }
        ]

        result = self.parser.parse_team_import_content(json.dumps(payload))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["email"], "first@example.com")
        self.assertEqual(result[1]["email"], "second@example.com")

    def test_parse_cockpit_tools_array_json(self):
        payload = [
            {
                "id": "codex_test_1",
                "email": "cockpit@example.com",
                "account_id": "123e4567-e89b-12d3-a456-426614174002",
                "tokens": {
                    "id_token": "eyJpZC50b2tlbg.sig.part",
                    "access_token": "eyJhY2Nlc3MuY29ja3BpdA.sig.part",
                    "refresh_token": "rt_cockpit.refresh-token"
                }
            }
        ]

        result = self.parser.parse_team_import_content(json.dumps(payload))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["token"], payload[0]["tokens"]["access_token"])
        self.assertEqual(result[0]["refresh_token"], payload[0]["tokens"]["refresh_token"])
        self.assertEqual(result[0]["account_id"], payload[0]["account_id"])
        self.assertEqual(result[0]["email"], payload[0]["email"])

    def test_reject_unsupported_json_shape(self):
        payload = {
            "foo": "bar",
            "items": [1, 2, 3]
        }

        with self.assertRaisesRegex(ValueError, "不是支持的 JSON 导入格式"):
            self.parser.parse_team_import_content(json.dumps(payload))

    def test_fallback_to_legacy_text_import(self):
        legacy_text = (
            "legacy@example.com----eyJsZWdhY3kudG9rZW4.sig.part----"
            "123e4567-e89b-12d3-a456-426614174003"
        )

        result = self.parser.parse_team_import_content(legacy_text)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["email"], "legacy@example.com")
        self.assertEqual(result[0]["account_id"], "123e4567-e89b-12d3-a456-426614174003")
        self.assertEqual(result[0]["token"], "eyJsZWdhY3kudG9rZW4.sig.part")

    async def test_import_team_batch_uses_json_parser_for_json_content(self):
        service = TeamService()
        service.import_team_single = AsyncMock(
            side_effect=[
                {
                    "success": True,
                    "team_id": 1,
                    "email": "cpa@example.com",
                    "message": "ok",
                    "error": None
                }
            ]
        )

        payload = {
            "access_token": "eyJiYXRjaC50b2tlbg.sig.part",
            "account_id": "123e4567-e89b-12d3-a456-426614174010",
            "email": "cpa@example.com"
        }

        events = [item async for item in service.import_team_batch(json.dumps(payload), None)]

        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[1]["type"], "progress")
        self.assertEqual(events[-1]["type"], "finish")
        self.assertEqual(events[1]["last_result"]["email"], "cpa@example.com")
        service.import_team_single.assert_awaited_once()

    async def test_import_team_batch_returns_error_for_unsupported_json(self):
        service = TeamService()

        payload = {"foo": "bar"}
        events = [item async for item in service.import_team_batch(json.dumps(payload), None)]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("不是支持的 JSON 导入格式", events[0]["error"])


if __name__ == "__main__":
    unittest.main()
