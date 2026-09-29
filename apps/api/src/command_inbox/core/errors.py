"""Application errors map 1:1 onto RFC 9457 problem details."""

from __future__ import annotations


class AppError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def bad_request(code: str, title: str, detail: str | None = None) -> AppError:
    return AppError(400, code, title, detail)


def unauthorized(title: str = "Sign in to continue") -> AppError:
    return AppError(401, "unauthenticated", title)


def forbidden(title: str, code: str = "forbidden") -> AppError:
    return AppError(403, code, title)


def not_found(what: str) -> AppError:
    return AppError(404, "not_found", f"{what} not found")


def conflict(code: str, title: str, detail: str | None = None) -> AppError:
    return AppError(409, code, title, detail)


def unprocessable(code: str, title: str, detail: str | None = None) -> AppError:
    return AppError(422, code, title, detail)
