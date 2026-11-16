"""Tests for service account key validation.

The critical property: no error message may ever contain private key
material. Earth Engine's own failure echoes the whole credential dict, which
would put a real private key on screen and into deployment logs.
"""

import json
import sys

sys.path.insert(0, ".")

from app.validation import GEEAuthError, validate_service_account_key  # noqa: E402

# A fake but structurally realistic private key, long enough to pass the
# length check. Not a real credential.
FAKE_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    + "\n".join(["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDaBcDeF" * 1] * 20)
    + "\n-----END PRIVATE KEY-----\n"
)

SECRET_MARKER = "SUPERSECRETKEYMATERIAL"


def good_key(**overrides):
    key = {
        "type": "service_account",
        "project_id": "arr-feasibility-123456",
        "private_key_id": "a1b2c3d4e5f6",
        "private_key": FAKE_PEM,
        "client_email": "gee-service@arr-feasibility-123456.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    key.update(overrides)
    return key


CASES = [
    # (label, key, must_raise, expected_substring)
    ("valid key", good_key(), False, None),
    (
        "the exact placeholder the user pasted",
        {
            "type": "service_account", "project_id": "...",
            "private_key_id": "...",
            "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
            "client_email": "gee-service@...", "client_id": "...",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        True, "template placeholders",
    ),
    (
        "missing fields",
        {"type": "service_account", "project_id": "x"},
        True, "missing",
    ),
    (
        "wrong key type (oauth client)",
        good_key(type="authorized_user"),
        True, "expected 'service_account'",
    ),
    (
        "placeholder project id only",
        good_key(project_id="your-gcp-project-id"),
        True, "template placeholders",
    ),
    (
        "truncated private key",
        good_key(private_key="-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----"),
        True, "truncated",
    ),
    (
        "not a PEM at all",
        good_key(private_key="just some text that is long enough " * 30),
        True, "does not contain a PEM",
    ),
    (
        "literal backslash-n (TOML double-quote damage)",
        good_key(private_key="-----BEGIN PRIVATE KEY-----\\n" + "A" * 900 + "\\n-----END PRIVATE KEY-----"),
        True, "SINGLE quotes",
    ),
    ("empty client_email", good_key(client_email=""), True, "placeholder"),
    ("not a dict", ["not", "a", "dict"], True, "JSON object"),
]


def main() -> int:
    failures = []

    for label, key, must_raise, expected in CASES:
        try:
            validate_service_account_key(key)
            raised, message = False, ""
        except GEEAuthError as exc:
            raised, message = True, str(exc)

        if raised != must_raise:
            failures.append(
                f"{label}: expected raise={must_raise}, got raise={raised}"
            )
            continue

        if must_raise and expected and expected.lower() not in message.lower():
            failures.append(
                f"{label}: message missing {expected!r}\n      got: {message[:120]}"
            )
            continue

        status = "raises" if raised else "passes"
        print(f"  {status:7} {label}")

    # --- The security property, checked explicitly -----------------------
    print("\nChecking no error message leaks key material:")
    leaky = good_key(
        private_key=f"-----BEGIN PRIVATE KEY-----\n{SECRET_MARKER}\n-----END",
        project_id="...",  # force a raise
    )
    try:
        validate_service_account_key(leaky)
        failures.append("expected a raise for the leak test")
    except GEEAuthError as exc:
        if SECRET_MARKER in str(exc):
            failures.append("LEAK: private key material appeared in the error message")
            print("  FAILED - key material found in message")
        else:
            print("  ok      private key absent from error message")

    # Same check for a key that is valid JSON but rejected for PEM shape.
    try:
        validate_service_account_key(good_key(private_key=SECRET_MARKER * 50))
        failures.append("expected a raise for the non-PEM leak test")
    except GEEAuthError as exc:
        if SECRET_MARKER in str(exc):
            failures.append("LEAK: key material in non-PEM error message")
            print("  FAILED - key material found in non-PEM message")
        else:
            print("  ok      non-PEM error also clean")

    if failures:
        print(f"\nFAILED - {len(failures)} problem(s):")
        for f in failures:
            print(f"  {f}")
        return 1

    print(f"\nAll {len(CASES)} validation cases pass; no message leaks key material.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
