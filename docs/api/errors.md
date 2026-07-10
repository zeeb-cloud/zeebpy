# Error Handling

Every error a Zeeb API returns uses one standardized, machine-readable JSON
envelope. Handlers installed by `install_exception_handlers()` (called
automatically by `create_app()`) convert **all** failures — raised Zeeb
exceptions, Pydantic/FastAPI validation errors, plain `HTTPException`s, ORM
lookups, and unhandled crashes — into this shape.

## The error envelope

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {
        "code": "FIELD_REQUIRED",
        "field": "email",
        "message": "Field required",
        "meta": {"input": null}
      },
      {
        "code": "FIELD_TOO_SHORT",
        "field": "password",
        "message": "String should have at least 8 characters",
        "meta": {"min_length": 8, "input": "abc"}
      }
    ],
    "meta": {
      "request_id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2024-01-15T10:30:00+00:00",
      "path": "/api/users/",
      "method": "POST"
    }
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `success` | bool | Always `false` on errors (success responses do not use this envelope). |
| `error.code` | str | Primary machine-readable code (see the taxonomy below). Branch on this, not on `message`. |
| `error.message` | str | Human-readable English text for debugging/logging — translate `code` client-side instead of displaying this. |
| `error.details[]` | list | Zero or more field-level errors: `code`, `field` (`null` for non-field errors), `message`, optional `meta` (constraint context such as `min_length`, `pattern`, truncated `input`). |
| `error.meta` | object | `request_id` (taken from an `X-Request-ID` / `X-Correlation-ID` / `Request-ID` header when present, else generated), ISO-8601 `timestamp`, request `path` and `method`. |

The Pydantic models behind the envelope — `ErrorResponse`, `ErrorBody`,
`ErrorDetail`, `ErrorMeta` — live in `zeeb_api.exceptions` and can be used in
OpenAPI annotations. `zeeb_api.exception_handlers.get_error_responses()`
returns ready-made `responses=` schemas for routes.

## Error code taxonomy

Codes follow `CATEGORY_SPECIFIC_ERROR` and are stable — frontends should key
i18n translations on them.

### Authentication — HTTP 401 (`AUTH_INSECURE_CONFIG` is 500)

| Code | Raised when |
|---|---|
| `AUTH_INVALID_CREDENTIALS` | Login with wrong email/password. |
| `AUTH_TOKEN_EXPIRED` | Access token past its expiry — refresh and retry. |
| `AUTH_TOKEN_INVALID` | Token malformed, bad signature, or wrong type. |
| `AUTH_TOKEN_MISSING` | No credentials on a protected endpoint (default for `AuthenticationException` / `AuthenticationFailed`). |
| `AUTH_SESSION_EXPIRED` | Session no longer valid. |
| `AUTH_INSECURE_CONFIG` | Insecure default secret with `DEBUG=false` (`InsecureSecretError`, 500 — the app refuses to serve). |
| `AUTH_OAUTH_STATE_INVALID` | OAuth `state`/PKCE check failed. |
| `AUTH_OAUTH_EXCHANGE_FAILED` | Authorization-code exchange with the provider failed. |
| `AUTH_OAUTH_ID_TOKEN_INVALID` | OIDC ID token failed validation. |
| `AUTH_OAUTH_PROVIDER_NOT_FOUND` | Unknown OAuth provider name in the callback path. |
| `AUTH_OAUTH_USER_NOT_PROVISIONED` | External identity valid but no local user exists and auto-provisioning is off. |

### Permissions — HTTP 403

| Code | Raised when |
|---|---|
| `PERM_DENIED` | Permission class rejected the request (default). |
| `PERM_INSUFFICIENT_ROLE` | Authenticated but the role/flag (e.g. staff) is missing. |
| `PERM_RESOURCE_FORBIDDEN` | Object-level permission rejected this specific resource. |

### Validation — HTTP 400 (raised) / 422 (request parsing)

Top-level codes: `VALIDATION_ERROR`, `VALIDATION_FAILED`. Field-level codes
appear in `error.details[]`:

`FIELD_REQUIRED`, `FIELD_INVALID_TYPE`, `FIELD_INVALID_FORMAT`,
`FIELD_INVALID_VALUE`, `FIELD_INVALID_CHOICE`, `FIELD_TOO_SHORT`,
`FIELD_TOO_LONG`, `FIELD_MIN_VALUE`, `FIELD_MAX_VALUE`, `FIELD_MIN_LENGTH`,
`FIELD_MAX_LENGTH`, `FIELD_UNIQUE_CONSTRAINT`, `FIELD_REGEX_MISMATCH`,
`FIELD_INVALID_EMAIL`, `FIELD_INVALID_URL`, `FIELD_INVALID_UUID`,
`FIELD_INVALID_DATE`, `FIELD_INVALID_DATETIME`, `FIELD_INVALID_JSON`.

Serializer-specific: `SERIALIZER_INVALID_DATA`, `SERIALIZER_MISSING_FIELDS`,
`SERIALIZER_READ_ONLY_FIELD` (400).

### Resources — HTTP 404 / 409 / 410

| Code | Status | Raised when |
|---|---|---|
| `RESOURCE_NOT_FOUND` | 404 | Lookup failed (incl. ORM `DoesNotExist`). `error.meta` may carry `resource_type` / `resource_id`. |
| `RESOURCE_ALREADY_EXISTS` | 409 | Create collides with an existing resource. |
| `RESOURCE_CONFLICT` | 409 | State conflict (incl. ORM `MultipleObjectsReturned`). |
| `RESOURCE_GONE` | 410 | Resource permanently removed. |

### Query / filter — HTTP 400

`QUERY_INVALID_FILTER`, `QUERY_INVALID_FIELD`, `QUERY_SYNTAX_ERROR`,
`QUERY_INVALID_OPERATOR`, `QUERY_INVALID_VALUE` — returned by the
`POST /<prefix>/query/` endpoint when a serialized `Q` filter string cannot be
parsed or references unknown fields/operators.

### Rate limiting — HTTP 429

`RATE_LIMIT_EXCEEDED`, `RATE_LIMIT_QUOTA_EXCEEDED`. The response carries a
`Retry-After` header (seconds); `error.meta.retry_after` mirrors it when set
via `RateLimitException(retry_after=...)`. See [Throttling](throttling.md).

### Versioning — HTTP 400

`API_VERSION_INVALID` — the requested API version is not in `ALLOWED_VERSIONS`.

### Server — HTTP 500 / 503

`SERVER_ERROR` (500, also the catch-all for unhandled exceptions),
`SERVER_MAINTENANCE`, `SERVER_UNAVAILABLE` (503), `SERVER_TIMEOUT`.

### Method — HTTP 405

`METHOD_NOT_ALLOWED` — response carries an `Allow` header listing permitted
methods.

## Raising errors in application code

**Always raise the canonical hierarchy from `zeeb_api.exceptions`** — never
ad-hoc `fastapi.HTTPException`s, and never import from `zeeb_api.response`
(a deprecated shim kept only for backward compatibility). This keeps every
response inside the envelope with a meaningful code.

### Typed exceptions

```python
from zeeb_api.exceptions import (
    ErrorCode,
    ResourceNotFoundException,
    ValidationException,
    PermissionException,
    field_error,
)

# 404 with resource context in error.meta
raise ResourceNotFoundException(
    message="Post not found", resource_type="Post", resource_id=post_id
)

# 400 with field-level details
raise ValidationException(
    message="Validation failed",
    details=[field_error("email", ErrorCode.FIELD_INVALID_EMAIL, "Invalid email")],
)

# 403 with a specific code
raise PermissionException(code=ErrorCode.PERM_INSUFFICIENT_ROLE, message="Staff only")
```

All typed exceptions subclass `ZeebException`, which accepts
`code`, `message`, `details`, `status_code`, `headers`, and `meta` for fully
custom errors. The full set: `ValidationException` (400),
`AuthenticationException` (401), `PermissionException` (403),
`ResourceNotFoundException` (404), `ResourceConflictException` (409),
`QueryException` (400), `RateLimitException` (429, sets `Retry-After`),
`ServerException` (500), `MethodNotAllowedException` (405, sets `Allow`),
`InsecureSecretError` (500). `ImproperlyConfigured` is a plain (non-HTTP)
configuration error.

### Shorthand aliases

For convenience, `zeeb_api.exceptions` also exports
`APIException`, `ValidationError`, `NotFound`, `PermissionDenied`,
`AuthenticationFailed`, `MethodNotAllowed`, and `Throttled`. They take a
`detail` argument (string, or `{field: [messages]}` for `ValidationError`,
which is flattened into `error.details[]`):

```python
from zeeb_api.exceptions import NotFound, ValidationError

raise NotFound("Post not found")
raise ValidationError({"email": ["Invalid email format"]})
```

These subclass **both** `ZeebException` and `fastapi.HTTPException`: with the
handlers installed they render the standardized envelope; without handlers,
Starlette's default `HTTPException` handling still produces the right status
code.

### Helpers

- `field_error(field, code, message, meta=None)` → an `ErrorDetail`.
- `validation_error(field, code, message, meta=None)` → a one-field
  `ValidationException`, ready to raise.
- `details_from_field_errors({field: [messages]})` → `list[ErrorDetail]`
  (each with code `FIELD_INVALID_VALUE`).

## The handler pipeline

`install_exception_handlers(app)` registers, in order:

1. **`ZeebException`** → rendered via `exc.to_response()` with the exception's
   own `status_code`, `code`, `details`, and `headers`.
2. **Pydantic `ValidationError` / FastAPI `RequestValidationError`** → 422
   `VALIDATION_ERROR`; each Pydantic error is mapped to a `FIELD_*` code
   (e.g. `missing` → `FIELD_REQUIRED`, `string_too_short` → `FIELD_TOO_SHORT`,
   `enum` → `FIELD_INVALID_CHOICE`, `uuid_parsing` → `FIELD_INVALID_UUID`),
   with constraint context and the (truncated) offending input in
   `details[].meta`. The `body`/`query`/`path` location prefix is stripped
   from `field`; nested paths are dotted (`"address.zip"`).
3. **Starlette/FastAPI `HTTPException`** → status code mapped through
   `STATUS_CODE_TO_ERROR_CODE` (400/422 → `VALIDATION_ERROR`,
   401 → `AUTH_TOKEN_MISSING`, 403 → `PERM_DENIED`,
   404 → `RESOURCE_NOT_FOUND`, 405 → `METHOD_NOT_ALLOWED`,
   409 → `RESOURCE_CONFLICT`, 429 → `RATE_LIMIT_EXCEEDED`,
   500 → `SERVER_ERROR`, 503 → `SERVER_UNAVAILABLE`; anything else →
   `SERVER_ERROR`). Dict `detail` payloads are flattened into field details.
4. **ORM exceptions** (when `zeeb_orm` is importable) —
   `DoesNotExist` → 404 `RESOURCE_NOT_FOUND`,
   `MultipleObjectsReturned` → 409 `RESOURCE_CONFLICT`. A bare
   `await Post.objects.get(pk=...)` in a handler therefore produces a correct
   404 without any try/except.
5. **Catch-all `Exception`** → 500 `SERVER_ERROR` with the generic message
   `"Internal server error"` — internals are never leaked; the full traceback
   is logged to the `zeeb_api` logger.

## ORM exceptions (`zeeb_orm.exceptions`)

The ORM layer raises plain exception classes with no HTTP semantics; codes are
attached only at the API boundary (see pipeline step 4 — everything not
explicitly mapped surfaces as a 500 unless your code catches it and raises a
`ZeebException`):

| Exception | Meaning |
|---|---|
| `ValidationError` | `full_clean()`/save-time validation failed. Carries `.message_dict` (`{field: [messages]}`, non-field errors under `"__all__"`) and `.messages` (flat list). Distinct from `zeeb_api.exceptions.ValidationError`. |
| `DoesNotExist` | Query returned no row (`.get()`). Handled → 404. |
| `MultipleObjectsReturned` | `.get()` matched more than one row. Handled → 409. |
| `ProtectedError` / `RestrictedError` | Delete blocked by `on_delete=PROTECT` / `RESTRICT`; carry `protected_objects` / `restricted_objects`. |
| `FieldError` / `FieldDoesNotExist` | Bad field lookup, expression, or unknown field name. |
| `NotSupportedError` | Operation unsupported by the database backend. |
| `TransactionManagementError` | Invalid transaction usage. |
