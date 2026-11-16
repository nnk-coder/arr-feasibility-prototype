"""Pure validation logic, with no third-party imports.

Everything here is plain Python and the standard library on purpose. These
checks guard the two failure modes that are expensive to debug in a
deployment — a malformed service account key, and an invalid Earth Engine
band name — so their tests must be runnable anywhere, including a fresh
checkout with nothing installed.

Importing them through `eligibility` or `gee_init` would drag in `ee` and
defeat that.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- band names

# Earth Engine band names must start with a letter and contain only letters,
# digits and underscores. A violation is only reported when the graph is
# evaluated on the server, so names are checked locally first.
_BAND_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def check_band_name(name: str) -> str:
    """Validate a band name, raising locally rather than at evaluation time."""
    if not _BAND_NAME_RE.match(name):
        raise ValueError(
            f"Invalid Earth Engine band name {name!r}. Band names must start "
            "with a letter and contain only letters, digits and underscores."
        )
    return name


# --------------------------------------------------------- service accounts


class GEEAuthError(RuntimeError):
    """Raised when Earth Engine cannot be initialised."""


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


def looks_like_placeholder(value: str) -> bool:
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
        if field in key and looks_like_placeholder(key[field])
    ]
    private_key = str(key.get("private_key", ""))
    if looks_like_placeholder(private_key) or "..." in private_key:
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


# ------------------------------------------------------------ error scrubbing

# Anything resembling PEM key material, so it can be stripped before an error
# reaches the screen or the deployment logs.
_PEM_PATTERN = re.compile(r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", re.DOTALL)


def scrub(text: str) -> str:
    """Remove key material from a message bound for display or logs."""
    text = _PEM_PATTERN.sub("[PRIVATE KEY REDACTED]", text)
    # Catch a bare key body that lost its PEM header in formatting.
    return re.sub(r"[A-Za-z0-9+/]{200,}={0,2}", "[REDACTED]", text)
