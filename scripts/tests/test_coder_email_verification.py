import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace


EXPRESSIONS = Path(__file__).resolve().parents[2] / "apps/authentik/coder-email"


def evaluate(name, request):
    expression = (EXPRESSIONS / f"{name}.expr").read_text()
    namespace = {}
    exec("def evaluate(request):\n" + textwrap.indent(expression, "    "), namespace)
    return namespace["evaluate"](request)


class User:
    def __init__(self, email="person@example.com", *, authenticated=True):
        self.pk = 42
        self.email = email
        self.database_email = email
        self.attributes = {}
        self.is_authenticated = authenticated
        self.saved = False

    def refresh_from_db(self, *, fields):
        self.email = self.database_email

    def save(self, *, update_fields):
        self.saved = update_fields == ["attributes"]


class CoderEmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.user = User()
        self.plan = SimpleNamespace(context={})
        self.request = SimpleNamespace(user=self.user, context={"flow_plan": self.plan})

    def token(self):
        return SimpleNamespace(
            user_id=self.user.pk,
            flow=SimpleNamespace(slug="coder-email-verification"),
            identifier="ak-email-stage-coder-email-confirmation-123",
            plan=SimpleNamespace(context=dict(self.plan.context)),
        )

    def test_only_authenticated_user_can_start_confirmation(self):
        self.user.is_authenticated = False
        self.assertFalse(evaluate("snapshot", self.request))
        self.assertEqual(self.plan.context, {})
        self.user.is_authenticated = True
        self.assertTrue(evaluate("snapshot", self.request))
        self.assertEqual(self.plan.context["email"], self.user.email)
        self.assertEqual(self.plan.context["coder_verification_email"], self.user.email)
        self.assertIs(self.plan.context["pending_user"], self.user)

    def test_verified_claim_requires_matching_current_address(self):
        self.assertFalse(evaluate("scope", self.request)["email_verified"])
        self.user.attributes["email_verified_address"] = self.user.email
        self.assertTrue(evaluate("scope", self.request)["email_verified"])
        self.user.email = "other@example.com"
        self.assertFalse(evaluate("scope", self.request)["email_verified"])
        self.user.email = "person@example.com"
        self.assertTrue(evaluate("scope", self.request)["email_verified"])
        self.user.attributes["email_verified_address"] = "other@example.com"
        self.assertFalse(evaluate("scope", self.request)["email_verified"])

    def test_record_requires_the_original_email_token_and_current_address(self):
        self.assertTrue(evaluate("snapshot", self.request))
        token = self.token()
        request = SimpleNamespace(context={"pending_user": self.user, "is_restored": token})
        self.assertTrue(evaluate("record", request))
        self.assertTrue(self.user.saved)
        self.assertEqual(self.user.attributes["email_verified_address"], "person@example.com")

        self.user.saved = False
        self.user.database_email = "other@example.com"
        # A newer session's merged context must not replace the address in the old token.
        request.context["flow_plan"] = SimpleNamespace(context={"coder_verification_email": "other@example.com"})
        self.assertFalse(evaluate("record", request))
        self.assertFalse(self.user.saved)

    def test_wrong_user_flow_stage_or_missing_snapshot_never_verifies(self):
        self.assertTrue(evaluate("snapshot", self.request))
        for change in ("user", "flow", "stage", "snapshot", "destination"):
            token = self.token()
            if change == "user":
                token.user_id = 99
            elif change == "flow":
                token.flow.slug = "other"
            elif change == "stage":
                token.identifier = "other-token"
            elif change == "snapshot":
                token.plan.context.pop("coder_verification_email")
            else:
                token.plan.context["email"] = "different@example.com"
            with self.subTest(change=change):
                self.user.saved = False
                request = SimpleNamespace(context={"pending_user": self.user, "is_restored": token})
                self.assertFalse(evaluate("record", request))
                self.assertFalse(self.user.saved)


if __name__ == "__main__":
    unittest.main()
