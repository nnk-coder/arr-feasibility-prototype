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


class GEEAuthError(RuntimeError):
    """Raised when Earth Engine cannot be initialised."""


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
            raise GEEAuthError(
                "GEE_CREDENTIALS_JSON is not valid JSON. It must be the "
                "service account key file contents on a single line."
            ) from exc
        try:
            creds = ee.ServiceAccountCredentials(
                key["client_email"], key_data=json.dumps(key)
            )
            ee.Initialize(credentials=creds, project=project)
        except KeyError as exc:
            raise GEEAuthError(
                f"Service account key is missing the {exc} field."
            ) from exc
        except Exception as exc:
            raise GEEAuthError(
                f"Earth Engine rejected the service account: {exc}\n\n"
                "Check that the account has the Earth Engine Resource Viewer "
                "role and that the Earth Engine API is enabled on the project."
            ) from exc
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
