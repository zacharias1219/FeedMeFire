"""Error envelope (PRD §13.2)."""
from __future__ import annotations

ERRORS: dict[str, tuple[int, str, bool]] = {
    # code: (http status, default message, retryable)
    "CONSENT_REQUIRED": (403, "Please accept every consent to continue.", False),
    "SESSION_EXPIRED": (401, "Your session has expired. Start a new one to continue.", False),
    "SAMPLE_CHANGED": (409, "Your sample changed after you approved it. Review and approve it again.", False),
    "INSUFFICIENT_ELIGIBLE_ITEMS": (422, "Add more eligible items to continue.", False),
    "INPUT_TOO_LARGE": (422, "That is more input than we can accept.", False),
    "GENERATION_IN_PROGRESS": (409, "A roast is already being generated for this session.", False),
    "RATE_LIMITED": (429, "You have reached a usage limit. Please try again later.", True),
    "QUEUE_FULL": (503, "We are at capacity right now. Please try again shortly.", True),
    "PROVIDER_UNAVAILABLE": (503, "The AI provider is unavailable right now. Please try again.", True),
    "GENERATION_TIMEOUT": (504, "Generation took too long. Please try again.", True),
    "UNSAFE_OUTPUT": (422, "We could not produce a roast that met our safety standards. Nothing was shown. Try a different intensity or sample.", True),
    "INVALID_MODEL_OUTPUT": (502, "The AI returned something we could not use. Please try again.", True),
    "REPORT_EXPIRED": (410, "This report has expired and was removed.", False),
    "DATA_DELETED": (410, "This data was deleted.", False),
    "NOT_FOUND": (404, "Not found.", False),
    "CSRF_FAILED": (403, "Request could not be verified. Reload the page and try again.", False),
    "AGE_ATTESTATION_REQUIRED": (422, "You must confirm you meet the age requirement to continue.", False),
    "INVALID_ITEM": (422, "That item cannot be used.", False),
    "VALIDATION_ERROR": (422, "The request was not valid.", False),
    "IDEMPOTENCY_KEY_REQUIRED": (422, "An Idempotency-Key header is required.", False),
    "SAMPLE_NOT_APPROVED": (409, "Approve the final sample before generating.", False),
    "REGENERATION_NOT_ALLOWED": (409, "This report cannot be regenerated.", False),
    "PROVIDER_MISCONFIGURED": (503, "The AI provider is not set up correctly on the server. If you run this server, check its logs.", False),
    "FEATURE_DISABLED": (503, "This feature is currently disabled.", True),
    "INTERNAL_ERROR": (500, "Something went wrong on our side.", True),
}


class AppError(Exception):
    def __init__(self, code: str, message: str | None = None, *, status: int | None = None,
                 retryable: bool | None = None, details: dict | None = None):
        s, m, r = ERRORS[code]
        self.code = code
        self.message = message or m
        self.status = status or s
        self.retryable = r if retryable is None else retryable
        self.details = details
        super().__init__(f"{code}: {self.message}")

    def envelope(self, request_id: str) -> dict:
        body = {"code": self.code, "message": self.message, "retryable": self.retryable, "request_id": request_id}
        if self.details:
            body["details"] = self.details
        return {"error": body}


def job_error(code: str | None) -> dict | None:
    if not code:
        return None
    s, m, r = ERRORS.get(code, ERRORS["INTERNAL_ERROR"])
    return {"code": code, "message": m, "retryable": r}
