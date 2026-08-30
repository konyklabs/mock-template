"""The documented ``ErrorMessage`` body, as a strict model.

DOCUMENTED verbatim on https://doc.toasttab.com/doc/devguide/apiResponsesAndErrors.html;
the shaper (``errors.py``) is the only producer. Key order is the page's, and
every key is emitted -- the nulls are on the page.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["ErrorMessageWire"]


class ErrorMessageWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: int
    code: int
    message: str
    requestId: str
    #: "reserved for future use" -- always null here.
    messageKey: str | None = None
    fieldName: str | None = None
    link: str | None = None
    developerMessage: str | None = None
    canRetry: bool | None = None

    def wire(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "messageKey": self.messageKey,
            "fieldName": self.fieldName,
            "link": self.link,
            "requestId": self.requestId,
            "developerMessage": self.developerMessage,
            "errors": [],
            "canRetry": self.canRetry,
        }
