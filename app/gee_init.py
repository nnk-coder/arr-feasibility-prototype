"""Earth Engine authentication.

Two paths: a service account for cloud deployment, and interactive
credentials for local development. Both end at the same initialised state.
"""

from __future__ import annotations

import json
import os

import ee

from app.config import GEE_CREDENTIALS_JSON, GEE_PROJECT

_initialised = False

# Fields a usable service account key must carry.
REQUIRED_KEY_FIELDS = (
    "type",
    "project_id",
    "private_key",
    "client_email",
    "token_uri",
)

# A real RSA 2048 private key in PEM form is around 1,700 characters. Anything
# much shorter is a truncated paste or a placeholder.
MIN_PRIVATE_KEY_CHARS = 800


class GEEAuthError(RuntimeError):
    """Raised when Earth Engine cannot be initialised."""


def _looks_like_placeholder(value: str) -> bool:
    """Detect template text that was pasted without substitution."""
    stripped = str(value).strip()
    if not stripped:
        return True
    # Bare ellipsis, or a value that is only dots and whitespace.
    if set(stripped) <= {".", " "}:
        return True
    # Values ending in an ellipsis, e.g. "gee-service@..."
    if stripped.endswith("..."):
        return True
    if stripped.startswith("your-") or stripped.startswith("<"):
        return True
    return False


def validate_service_account_key(key: dict) -> None:
    """Check a parsed service account key before handing it to Earth Engine.

    Earth Engine's own failure for a bad key echoes the entire credential dict
    — private key included — into its exception message. Catching the common
    mistakes here means the user gets a precise, safe error instead.

    No message raised from this function contains key material.
    """
    if not isinstance(key, dict):
        raise GEEAuthError(
            "GEE_CREDENTIALS_JSON did not parse to a JSON object. It must be "
            "the contents of the service account key file."
        )

    missing = [f for f in REQUIRED_KEY_FIELDS if f not in key]
    if missing:
        raise GEEAuthError(
            f"Service account key is missing: {', '.join(missing)}.\n\n"
            "Use the whole downloaded .json file, not an excerpt."
        )

    if key.get("type") != "service_account":
        raise GEEAuthError(
            f"Key has type '{key.get('type')}', expected 'service_account'. "
            "This looks like an OAuth client key rather than a service "
            "account key — download the right one from IAM & Admin → "
            "Service Accounts → Keys."
        )

    # Placeholder detection, reported by field name only.
    placeholders = [
        field
        for field in ("project_id", "client_email", "private_key_id")
        if field in key and _looks_like_placeholder(key[field])
    ]
    private_key = str(key.get("private_key", ""))
    if _looks_like_placeholder(private_key) or "..." in private_key:
        placeholders.append("private_key")

    if placeholders:
        raise GEEAuthError(
            "The credentials still contain template placeholders rather than "
            f"real values: {', '.join(sorted(set(placeholders)))}.\n\n"
            "Open the .json key file you downloaded from Google Cloud, copy "
            "its entire contents, and paste that in place of the example. "
            "Do not retype or edit it by hand."
        )

    if "BEGIN PRIVATE KEY" not in private_key:
        raise GEEAuthError(
            "The private_key field does not contain a PEM private key. "
            "Paste the key file contents unmodified."
        )

    if len(private_key) < MIN_PRIVATE_KEY_CHARS:
        raise GEEAuthError(
            f"The private_key field is only {len(private_key)} characters; a "
            f"real key is around 1,700. It was probably truncated when "
            "pasted. Copy the whole .json file contents again."
        )

    if "\\n" in private_key and "\n" not in private_key:
        raise GEEAuthError(
            "The private key's line breaks arrived as literal backslash-n "
            "rather than newlines.\n\n"
            "In Streamlit secrets, wrap the JSON in SINGLE quotes, not "
            "double. TOML processes escape sequences inside double-quoted "
            "strings, which corrupts the key."
        )


def _credentials_json() -> str:
    """Service account JSON from env or Streamlit secrets, if present."""
    if GEE_CREDENTIALS_JSON:
        return GEE_CREDENTIALS_JSON

    # Streamlit Cloud puts secrets in st.secrets rather than the environment.
    try:
        import streamlit as st

        return st.secrets.get("GEE_CREDENTIALS_JSON", "")
    except Exception:
        return ""


def _project() -> str:
    if GEE_PROJECT:
        return GEE_PROJECT
    try:
        import streamlit as st

        return st.secrets.get("GEE_PROJECT", "")
    except Exception:
        return ""


def initialize(force: bool = False) -> None:
    """Initialise Earth Engine exactly once per process."""
    global _initialised
    if _initialised and not force:
        return

    project = _project()
    if not project:
        raise GEEAuthError(
            "GEE_PROJECT is not set. Add it to your .env file locally, or to "
            "Streamlit secrets when deployed."
        )

    key_json = _credentials_json()

    if key_json:
        try:
            key = json.loads(key_json)
        except json.JSONDecodeError as exc:
            # A raw newline inside a JSON string is invalid, and it is the
            # signature of TOML having processed the private key's \n escapes
            # because the value was wrapped in double quotes.
            if "control character" in str(exc).lower():
                raise GEEAuthError(
                    "The credentials are not valid JSON because the private "
                    "key's line breaks were converted to real newlines.\n\n"
                    "In Streamlit secrets, wrap the JSON in SINGLE quotes:\n"
                    "    GEE_CREDENTIALS_JSON = '{\"type\":\"service_account\",…}'\n\n"
                    "Double quotes make TOML process the \\n escapes, which "
                    "corrupts the key."
                ) from None
            raise GEEAuthError(
                "GEE_CREDENTIALS_JSON is not valid JSON. It must be the "
                "contents of the service account key file, on a single line, "
                "wrapped in single quotes."
            ) from None
        validate_service_account_key(key)

        try:
            creds = ee.ServiceAccountCredentials(
                key["client_email"], key_data=json.dumps(key)
            )
            ee.Initialize(credentials=creds, project=project)
        except Exception as exc:
            # Never interpolate the exception text here. Earth Engine and
            # google-auth both echo the whole credential dict — including the
            # private key — into their error messages, which would put it on
            # screen and into the deployment logs.
            raise GEEAuthError(
                "Earth Engine rejected the service account "
                f"({type(exc).__name__}).\n\n"
                "The key parsed correctly, so the likely causes are:\n"
                "• The service account does not have the Earth Engine "
                "Resource Viewer role on the project\n"
                "• The Earth Engine API is not enabled on the project\n"
                "• The project is not registered with Earth Engine — open "
                "code.earthengine.google.com once and select it\n\n"
                "The underlying error was suppressed because it contains the "
                "private key."
            ) from None
    else:
        # Local development path — relies on `earthengine authenticate`
        # having been run once in this environment.
        try:
            ee.Initialize(project=project)
        except Exception as exc:
            raise GEEAuthError(
                f"Earth Engine could not initialise: {exc}\n\n"
                "Locally, run `earthengine authenticate` once. For deployment, "
                "set GEE_CREDENTIALS_JSON to your service account key."
            ) from exc

    _initialised = True


def is_initialised() -> bool:
    return _initialised
