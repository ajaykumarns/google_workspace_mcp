"""Action limits for the personal Bifrost Workspace service."""

import json
import os
from email.parser import BytesParser
from email.policy import default

from googleapiclient.http import HttpRequest


POLICY_ENV = "WORKSPACE_MCP_ACTION_POLICY"
POLICY_NAME = "organize-no-delete"
GMAIL_WRITES = frozenset({
    "gmail.users.messages.modify",
    "gmail.users.messages.batchModify",
    "gmail.users.threads.modify",
    "gmail.users.labels.create",
    "gmail.users.labels.update",
    "gmail.users.labels.patch",
})


def policy_enabled():
    value = os.getenv(POLICY_ENV, "").strip()
    if value not in ("", POLICY_NAME):
        raise ValueError(f"Unsupported {POLICY_ENV} value")
    return value == POLICY_NAME


def reject_delete(action):
    if policy_enabled():
        raise ValueError(f"Action blocked by Workspace no-delete policy: {action}")


def _metadata(request):
    body = request.body
    if not body:
        return {}
    content_type = next(
        (v for k, v in request.headers.items() if k.lower() == "content-type"),
        "application/json",
    )
    raw = body.encode() if isinstance(body, str) else body
    if content_type.lower().startswith("multipart/"):
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
        )
        for part in message.walk():
            if part.get_content_type() == "application/json":
                return json.loads(part.get_payload(decode=True))
        raise ValueError("Workspace policy cannot inspect upload metadata")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Workspace policy requires object metadata")
    return value


def validate_write_metadata(method_id, metadata):
    if not policy_enabled():
        return
    if method_id in GMAIL_WRITES:
        labels = metadata.get("addLabelIds", []) + metadata.get("removeLabelIds", [])
        if any(str(label).strip().upper() in {"TRASH", "SPAM"} for label in labels):
            reject_delete("Gmail Trash or Spam label change")
    elif method_id.startswith(("drive.files.", "calendar.events.")):
        if metadata.get("trashed") not in (None, False):
            reject_delete("Drive Trash update")
        if str(metadata.get("status", "")).lower() == "cancelled":
            reject_delete("Calendar event cancellation")


def protected_request(*args, **kwargs):
    """Reject forbidden requests before Google receives any request."""
    request = HttpRequest(*args, **kwargs)
    if not policy_enabled():
        return request
    method_id = request.methodId or ""
    action = method_id.rsplit(".", 1)[-1].lower()
    if request.method.upper() == "DELETE" or action in {
        "delete", "batchdelete", "trash", "emptytrash", "clear",
    }:
        reject_delete(method_id)
    if request.method.upper() == "GET":
        return request
    if method_id.startswith("gmail.") and method_id not in GMAIL_WRITES:
        raise ValueError("Action blocked by Workspace Gmail organize-only policy")
    if method_id in GMAIL_WRITES or method_id.startswith(("drive.files.", "calendar.events.")):
        validate_write_metadata(method_id, _metadata(request))
    return request
