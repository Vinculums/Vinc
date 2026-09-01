"""앱 구조 드리프트 게이트의 투영 계약 — az 없이 순수 함수만 검사한다.

게이트의 값은 두 가지 성질에 있다: (1) 값이 아니라 구조만 든다(시크릿·env 값이
기준선 파일에 새면 그 파일 자체가 유출면이 된다), (2) 사고가 났던 필드
(excludedPaths·앱 부재)의 변화가 투영 차이로 드러난다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gate_app_config import project_app   # noqa: E402


def _show(env=None, excluded=None):
    return {"properties": {
        "template": {
            "containers": [{
                "name": "app",
                "env": [{"name": n, "value": "SECRET-VALUE"} for n in (env or [])],
                "resources": {"cpu": 1.0, "memory": "2Gi"},
                "volumeMounts": [{"volumeName": "data", "mountPath": "/data"}],
            }],
            "scale": {"minReplicas": 1, "maxReplicas": 1,
                      "cooldownPeriod": 300},
            "volumes": [{"name": "data", "storageType": "AzureFile",
                         "storageName": "share"}],
        },
        "configuration": {
            "ingress": {"external": True, "targetPort": 8080,
                        "transport": "Auto", "allowInsecure": False,
                        "customDomains": [{"name": "api.example"}]},
            "secrets": [{"name": "db-password", "value": "HUSH"}],
        },
    }}


def _auth(excluded):
    return {"globalValidation": {"excludedPaths": excluded,
                                 "unauthenticatedClientAction": "RedirectToLoginPage"},
            "identityProviders": {"azureStaticWebApps": {"enabled": True}},
            "platform": {"enabled": True}}


class ProjectionTests(unittest.TestCase):

    def test_값은_투영에_실리지_않는다(self):
        """env 값·시크릿 값이 기준선에 새면 기준선 파일이 유출면이 된다."""
        p = project_app(_show(env=["DB_PASSWORD"]), {})
        flat = repr(p)
        self.assertNotIn("SECRET-VALUE", flat)
        self.assertNotIn("HUSH", flat)
        self.assertEqual(p["env_names"], ["DB_PASSWORD"])
        self.assertEqual(p["secret_names"], ["db-password"])

    def test_excludedPaths_변화가_투영을_가른다(self):
        """08-30 사고의 그 필드 — 소실되면 투영이 달라져 게이트가 운다."""
        before = project_app(_show(), _auth(["/internal/mcp/verify-key"]))
        after = project_app(_show(), _auth([]))
        self.assertNotEqual(before, after)
        self.assertEqual(before["auth"]["excludedPaths"],
                         ["/internal/mcp/verify-key"])
        self.assertEqual(after["auth"]["excludedPaths"], [])

    def test_auth_없음은_None_이고_있음과_구분된다(self):
        """hub 처럼 플랫폼 인증이 없는 앱({})과 빈 excludedPaths 인증은 다른 상태다."""
        none = project_app(_show(), {})
        empty = project_app(_show(), _auth([]))
        self.assertIsNone(none["auth"])
        self.assertIsNotNone(empty["auth"])

    def test_배포마다_바뀌는_필드는_없다(self):
        """이미지·리비전이 투영에 들어가면 매 배포가 드리프트가 된다."""
        show = _show()
        show["properties"]["template"]["containers"][0]["image"] = "acr/app:sha1"
        show["properties"]["template"]["revisionSuffix"] = "r123"
        a = project_app(show, {})
        show["properties"]["template"]["containers"][0]["image"] = "acr/app:sha2"
        show["properties"]["template"]["revisionSuffix"] = "r124"
        b = project_app(show, {})
        self.assertEqual(a, b)

    def test_구조_필드는_결정론으로_정렬된다(self):
        """env 순서가 달라도 투영은 같아야 한다 — az 출력 순서는 계약이 아니다."""
        a = project_app(_show(env=["B", "A"]), {})
        b = project_app(_show(env=["A", "B"]), {})
        self.assertEqual(a, b)
        self.assertEqual(a["env_names"], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
