"""Policy tests use real discovery clients without sending Google requests."""

import asyncio
import os
import unittest
from unittest.mock import Mock, patch

from google.auth.credentials import AnonymousCredentials
from googleapiclient.http import MediaInMemoryUpload

from auth.action_policy import POLICY_ENV, POLICY_NAME, protected_request
from auth.google_auth import build_google_service
from gdrive.drive_helpers import initiate_resumable_upload_session


class BifrostActionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {POLICY_ENV: POLICY_NAME})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.clients = {
            name: build_google_service(name, version, AnonymousCredentials())
            for name, version in (("gmail", "v1"), ("drive", "v3"), ("calendar", "v3"))
        }
        for client in self.clients.values():
            client._http.request = Mock(
                side_effect=AssertionError("Network is forbidden")
            )
            self.addCleanup(client.close)

    def test_allowed_gmail_organization(self):
        gmail = self.clients["gmail"]
        for body in (
            {"removeLabelIds": ["INBOX"]},
            {"removeLabelIds": ["UNREAD"]},
            {"addLabelIds": ["UNREAD"]},
            {"addLabelIds": ["Label_123"], "removeLabelIds": ["INBOX"]},
        ):
            with self.subTest(body=body):
                gmail.users().messages().modify(userId="me", id="test", body=body)
                gmail.users().messages().batchModify(
                    userId="me", body={"ids": ["test"], **body}
                )
                gmail.users().threads().modify(userId="me", id="test", body=body)

    def test_gmail_trash_and_spam_are_blocked_in_all_label_paths(self):
        gmail = self.clients["gmail"]
        for label in ("TRASH", "SPAM", "trash", " spam "):
            for field in ("addLabelIds", "removeLabelIds"):
                for resource, batch in (
                    (gmail.users().messages(), False),
                    (gmail.users().messages(), True),
                    (gmail.users().threads(), False),
                ):
                    with (
                        self.subTest(label=label, field=field, batch=batch),
                        self.assertRaisesRegex(ValueError, "no-delete"),
                    ):
                        if batch:
                            resource.batchModify(
                                userId="me", body={"ids": ["test"], field: [label]}
                            )
                        else:
                            resource.modify(
                                userId="me", id="test", body={field: [label]}
                            )

    def test_gmail_send_draft_and_delete_are_blocked(self):
        gmail = self.clients["gmail"]
        calls = (
            lambda: gmail.users().messages().send(userId="me", body={"raw": "test"}),
            lambda: (
                gmail.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": "test"}})
            ),
            lambda: gmail.users().messages().trash(userId="me", id="test"),
            lambda: gmail.users().messages().delete(userId="me", id="test"),
            lambda: (
                gmail.users()
                .messages()
                .batchDelete(userId="me", body={"ids": ["test"]})
            ),
            lambda: gmail.users().labels().delete(userId="me", id="Label_test"),
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaisesRegex(ValueError, "policy"):
                call()

    def test_drive_create_edit_move_copy_and_share_are_allowed(self):
        drive = self.clients["drive"]
        drive.files().create(
            body={"name": "test", "mimeType": "application/vnd.google-apps.folder"}
        )
        drive.files().update(
            fileId="test",
            body={"name": "renamed"},
            addParents="parent",
            removeParents="old",
        )
        drive.files().copy(fileId="test", body={"name": "copy"})
        drive.permissions().create(
            fileId="test",
            body={"type": "user", "role": "reader", "emailAddress": "test@example.com"},
        )

    def test_drive_delete_trash_and_empty_trash_are_blocked(self):
        drive = self.clients["drive"]
        calls = (
            lambda: drive.files().delete(fileId="test"),
            lambda: drive.files().emptyTrash(),
            lambda: drive.files().update(fileId="test", body={"trashed": True}),
            lambda: drive.files().create(body={"name": "test", "trashed": True}),
        )
        for call in calls:
            with (
                self.subTest(call=call),
                self.assertRaisesRegex(ValueError, "no-delete"),
            ):
                call()

    def test_drive_uploads_cannot_bypass_trash_block(self):
        drive = self.clients["drive"]
        for resumable in (False, True):
            media = MediaInMemoryUpload(
                b"test", mimetype="text/plain", resumable=resumable
            )
            with (
                self.subTest(resumable=resumable),
                self.assertRaisesRegex(ValueError, "no-delete"),
            ):
                drive.files().update(
                    fileId="test", body={"trashed": True}, media_body=media
                )
        with self.assertRaisesRegex(ValueError, "no-delete"):
            asyncio.run(
                initiate_resumable_upload_session(
                    drive,
                    upload_mime_type="text/plain",
                    file_id="test",
                    file_metadata={"trashed": True},
                )
            )

    def test_drive_content_uploads_are_allowed(self):
        for resumable in (False, True):
            with self.subTest(resumable=resumable):
                media = MediaInMemoryUpload(
                    b"test", mimetype="text/plain", resumable=resumable
                )
                self.clients["drive"].files().update(
                    fileId="test", body={"name": "updated"}, media_body=media
                )
                for content, mime_type in (
                    (b"test", "text/plain"),
                    (b'{"trashed":true}', "application/json"),
                ):
                    media = MediaInMemoryUpload(
                        content, mimetype=mime_type, resumable=resumable
                    )
                    self.clients["drive"].files().update(
                        fileId="test", media_body=media
                    )

    def test_calendar_create_update_rsvp_are_allowed(self):
        calendar = self.clients["calendar"]
        calendar.calendars().insert(body={"summary": "test"})
        calendar.events().insert(calendarId="primary", body={"summary": "test"})
        calendar.events().patch(
            calendarId="primary", eventId="test", body={"summary": "updated"}
        )
        calendar.events().patch(
            calendarId="primary",
            eventId="test",
            body={
                "attendees": [
                    {"email": "test@example.com", "responseStatus": "accepted"}
                ]
            },
        )
        calendar.freebusy().query(body={"items": [{"id": "primary"}]})

    def test_upstream_comment_and_recurrence_actions_keep_policy_limits(self):
        drive = self.clients["drive"]
        drive.comments().update(
            fileId="test", commentId="test", body={"content": "updated"}, fields="id"
        )
        with self.assertRaisesRegex(ValueError, "no-delete"):
            drive.comments().delete(fileId="test", commentId="test")
        self.clients["calendar"].events().patch(
            calendarId="primary",
            eventId="test",
            body={"recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20261101T000000Z"]},
        )

    def test_calendar_delete_clear_and_cancel_are_blocked(self):
        calendar = self.clients["calendar"]
        calls = (
            lambda: calendar.events().delete(calendarId="primary", eventId="test"),
            lambda: calendar.calendars().delete(calendarId="test"),
            lambda: calendar.calendars().clear(calendarId="test"),
            lambda: calendar.events().patch(
                calendarId="primary", eventId="test", body={"status": "cancelled"}
            ),
            lambda: calendar.events().update(
                calendarId="primary", eventId="test", body={"status": "cancelled"}
            ),
        )
        for call in calls:
            with (
                self.subTest(call=call),
                self.assertRaisesRegex(ValueError, "no-delete"),
            ):
                call()

    def test_invalid_configuration_fails_closed(self):
        with (
            patch.dict(os.environ, {POLICY_ENV: "typo"}),
            self.assertRaisesRegex(ValueError, POLICY_ENV),
        ):
            protected_request(None, None, "https://example.com", method="GET")


if __name__ == "__main__":
    unittest.main()
