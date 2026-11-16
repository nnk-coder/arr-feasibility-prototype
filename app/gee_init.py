"""Earth Engine authentication.

Two paths: a service account for cloud deployment, and interactive
credentials for local development. Both end at the same initialised state.
"""

from __future__ import annotations

import json
import os
import re

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


def _from_streamlit_secrets(name: str) -> str:
    try:
        import streamlit as st

        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


def _resolve(name: str, env_value: str) -> tuple[str, str]:
    """Resolve a setting, returning (value, source).

    Streamlit secrets take precedence over the environment. This order
    matters: config.py calls load_dotenv() at import, so a .env file that
    found its way into the repository would otherwise silently override the
    Secrets box on a deployment — and the resulting failure looks like the
    secrets never saved.
    """
    secret = _from_streamlit_secrets(name)
    if secret:
        return secret, "Streamlit secrets"
    if env_value:
        return env_value, "environment or .env file"
    return "", "not set"


def _credentials_json() -> tuple[str, str]:
    return _resolve("GEE_CREDENTIALS_JSON", GEE_CREDENTIALS_JSON)


def _project() -> tuple[str, str]:
    return _resolve("GEE_PROJECT", GEE_PROJECT)


def describe_config() -> dict:
    """Where settings are coming from, with nothing sensitive in the output.

    Shown in the app's diagnostics panel so a misconfigured deployment can be
    identified without guessing.
    """
    project, project_source = _project()
    creds, creds_source = _credentials_json()

    info = {
        "project": project or "(not set)",
        "project_source": project_source,
        "credentials_source": creds_source,
        "credentials_present": bool(creds),
    }

    if creds:
        info["credentials_length"] = len(creds)
        try:
            key = json.loads(creds)
            # Identifying, non-secret fields only. Never the private key.
            info["key_client_email"] = key.get("client_email", "(absent)")
            info["key_project_id"] = key.get("project_id", "(absent)")
            info["key_type"] = key.get("type", "(absent)")
            pk = str(key.get("private_key", ""))
            info["private_key_chars"] = len(pk)
            info["private_key_looks_real"] = (
                "BEGIN PRIVATE KEY" in pk
                and len(pk) >= MIN_PRIVATE_KEY_CHARS
                and "..." not in pk
            )
        except json.JSONDecodeError as exc:
            info["parse_error"] = f"{type(exc).__name__}: {exc.msg}"

    return info


def initialize(force: bool = False) -> None:
    """Initialise Earth Engine exactly once per process."""
    global _initialised
    if _initialised and not force:
        return

    project, project_source = _project()
    key_json, creds_source = _credentials_json()

    # The project id carried inside the service account key. Used to give a
    # precise instruction when GEE_PROJECT is missing or still a placeholder,
    # rather than making the user go and look it up.
    key_project = ""
    if key_json:
        try:
            key_project = str(json.loads(key_json).get("project_id", "") or "")
        except json.JSONDecodeError:
            key_project = ""

    if not project:
        if key_project:
            # Nothing was configured, but the key names its project. Using it
            # is almost always right, so proceed rather than block.
            project, project_source = key_project, "service account key"
        else:
            raise GEEAuthError(
                "GEE_PROJECT is not set. Add it to Streamlit secrets when "
                "deployed, or to your .env file locally."
            )
    elif _looks_like_placeholder(project):
        # Set, but still template text. This is a configuration error worth
        # surfacing rather than silently working around.
        hint = (
            f"\n\nYour service account key says the project is "
            f"'{key_project}'. Set:\n    GEE_PROJECT = \"{key_project}\""
            if key_project
            else ""
        )
        raise GEEAuthError(
            f"GEE_PROJECT is still the template value '{project}' rather "
            f"than a real project id (read from: {project_source})."
            + hint
        )

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
        try:
            validate_service_account_key(key)
        except GEEAuthError as exc:
            # Naming the source is the whole diagnosis when a stray .env in
            # the repository is shadowing the Secrets box.
            raise GEEAuthError(
                f"{exc}\n\nThese credentials were read from: {creds_source}."
                + (
                    "\n\nStreamlit secrets were NOT used. If you updated the "
                    "Secrets box and are still seeing this, a .env file is "
                    "committed to your repository and is overriding it — "
                    "delete it and push."
                    if creds_source != "Streamlit secrets"
                    else ""
                )
            ) from None

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


# Anything resembling PEM key material, so it can be stripped before an error
# reaches the screen or the deployment logs.
_PEM_PATTERN = re.compile(
    r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", re.DOTALL
)


def safe_error(exc: Exception) -> str:
    """Render an exception for display with key material removed.

    Earth Engine and google-auth both echo credential dictionaries into their
    exception messages. Any error surfaced in the UI passes through here, so a
    future code path cannot put a private key on screen by accident.
    """
    text = str(exc)
    text = _PEM_PATTERN.sub("[PRIVATE KEY REDACTED]", text)
    # Catch a bare key body that lost its PEM header in formatting.
    text = re.sub(r"[A-Za-z0-9+/]{200,}={0,2}", "[REDACTED]", text)

    hint = _hint_for(text)
    return f"{text}{hint}"


def _hint_for(message: str) -> str:
    """Map known Google Cloud failures to the action that resolves them."""
    lowered = message.lower()

    if "serviceusage" in lowered or "serviceusageconsumer" in lowered:
        return (
            "\n\n**What this means:** the service account can authenticate "
            "but is not permitted to call APIs on the project.\n\n"
            "Add the **Service Usage Consumer** role to "
            "the service account in IAM & Admin → IAM, then wait 2–5 minutes "
            "for the permission to propagate."
        )
    if "earthengine.maps.create" in lowered:
        return (
            "\n\n**What this means:** the service account can read Earth "
            "Engine but cannot generate map tiles. The **Earth Engine "
            "Resource Viewer** role only grants `earthengine.maps.get`; "
            "rendering the overlay needs `earthengine.maps.create`.\n\n"
            "Replace that role with **Earth Engine Resource Writer** in "
            "IAM & Admin → IAM, then wait 2–5 minutes. The app never writes "
            "to your assets — Google classifies tile generation as creating "
            "a map resource."
        )
    if "earthengine." in lowered and "denied" in lowered:
        return (
            "\n\n**What this means:** the service account is missing an Earth "
            "Engine permission. It should hold **Earth Engine Resource "
            "Writer** and **Service Usage Consumer**."
        )
    if "earth engine" in lowered and "not registered" in lowered:
        return (
            "\n\n**What this means:** the Cloud project is not registered "
            "with Earth Engine. Open code.earthengine.google.com once and "
            "select this project."
        )
    if "api has not been used" in lowered or "api is not enabled" in lowered:
        return (
            "\n\n**What this means:** the Earth Engine API is not enabled. "
            "Enable it under APIs & Services → Library."
        )
    if "quota" in lowered or "resource_exhausted" in lowered:
        return (
            "\n\n**What this means:** an Earth Engine quota was hit. Try a "
            "smaller area, or wait before retrying."
        )
    if "computation timed out" in lowered or "deadline" in lowered:
        return (
            "\n\n**What this means:** the computation exceeded Earth Engine's "
            "time limit. Try a smaller boundary."
        )
    return ""
