#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from unittest import mock

import pytest
from restalchemy.common import exceptions as ra_e

from exordos_notification.dm import models


def test_explicit_recipient_is_rendered_and_used_by_smtp():
    template = models.EmailContent(
        title="Confirm", bodies=["{{ code }}"], recipient="{{ recipient }}"
    )
    content = template.render({"recipient": "new@example.com", "code": "one-time"})
    assert content.recipient == "new@example.com"
    # A queued email must retain its recipient after the user's address changes.
    kind_type = models.types_dynamic.KindModelType(models.RenderedEmailContent)
    restored = kind_type.from_simple_type(kind_type.to_simple_type(content))
    protocol = models.SimpleSmtpProtocol(
        host="localhost", port=25, noreply_email_address="sender@example.com"
    )
    with mock.patch.object(models.smtplib, "SMTP") as smtp:
        protocol.send(restored, {"user": {"email": "later@example.com"}})
        kwargs = smtp.return_value.__enter__.return_value.sendmail.call_args.kwargs
    assert kwargs["to_addrs"] == "new@example.com"
    assert "To: new@example.com" in kwargs["msg"]


def test_event_parameters_cannot_override_recipient_without_template_opt_in():
    content = models.EmailContent(title="Default").render(
        {"recipient": "other@example.com"}
    )
    assert content.recipient is None
    protocol = models.SimpleSmtpProtocol(
        host="localhost", port=25, noreply_email_address="sender@example.com"
    )
    assert (
        protocol._build_message(content, {"user": {"email": "user@example.com"}})["To"]
        == "user@example.com"
    )


@pytest.mark.parametrize(
    "recipient", ["invalid", "victim@example.com\r\nBcc: other@example.com", ""]
)
def test_invalid_recipient_is_rejected(recipient):
    with pytest.raises(ra_e.TypeError):
        models.EmailContent(recipient="{{ recipient }}").render(
            {"recipient": recipient}
        )


def test_missing_recipient_does_not_fall_back_to_current_address():
    with pytest.raises(models.jinja2.UndefinedError):
        models.EmailContent(recipient="{{ recipient }}").render({})
