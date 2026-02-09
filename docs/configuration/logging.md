# Logging

Zeeb provides a flexible logging system with JSON support, colored console output, and automatic rotation.

## Quick Start

Logging is automatically configured for new projects:

```python
# settings.py
LOGGING = {
    "level": "INFO",
}
```

Use in your code:

```python
from zeeb_api.logging import get_logger

logger = get_logger(__name__)

logger.info("User logged in", user_id=123)
logger.error("Failed to process payment", order_id=456, error="Invalid card")
```

## Configuration

### Basic Configuration

```python
# settings.py
LOGGING = {
    "level": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
}
```

### File Logging

```python
LOGGING = {
    "level": "INFO",
    "log_file": "logs/app.log",
}
```

### JSON Logging (Production)

```python
LOGGING = {
    "level": "INFO",
    "json_logs": True,
    "log_file": "logs/app.log",
}
```

Output:
```json
{"timestamp": "2024-01-15T10:30:00Z", "level": "INFO", "logger": "myapp", "message": "User logged in", "user_id": 123}
```

### Log Rotation

```python
LOGGING = {
    "level": "INFO",
    "log_file": "logs/app.log",
    "log_rotation": True,
    "log_retention_days": 30,
}
```

Logs rotate at midnight. Old logs are named `app.log.2024-01-15`.

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `level` | `"INFO"` | Minimum log level |
| `json_logs` | `False` | Output JSON format |
| `log_file` | `None` | File path for logs |
| `log_rotation` | `True` | Rotate logs daily |
| `log_retention_days` | `30` | Days to keep old logs |

## Using the Logger

### Get a Logger

```python
from zeeb_api.logging import get_logger

# Named logger
logger = get_logger(__name__)

# Logger with default extra fields
logger = get_logger("payment", service="payment-processor", version="1.0")
```

### Log Levels

```python
logger.debug("Detailed debugging info")
logger.info("General information")
logger.warning("Something unexpected")
logger.error("Error occurred")
logger.critical("System failure")
```

### Extra Fields

Pass extra fields as keyword arguments:

```python
logger.info("Order created", order_id=123, user_id=456, total=99.99)
```

Console output:
```
2024-01-15 10:30:00 | INFO | myapp | Order created | order_id=123 | user_id=456 | total=99.99
```

JSON output:
```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "INFO",
  "logger": "myapp",
  "message": "Order created",
  "order_id": 123,
  "user_id": 456,
  "total": 99.99
}
```

### Request Logging

```python
from zeeb_api.logging import get_request_logger

async def my_endpoint(request):
    # Logger with request_id for tracing
    logger = get_request_logger(request.state.request_id)
    
    logger.info("Processing request", path=request.url.path)
    # All logs include request_id automatically
```

### Exception Logging

```python
try:
    process_payment()
except Exception as e:
    logger.exception("Payment failed", order_id=123)
    # Includes full stack trace
```

## Console Formatter

For development, console output is colorized:

- 🟦 DEBUG - Blue
- 🟩 INFO - Green  
- 🟨 WARNING - Yellow
- 🟥 ERROR - Red
- 🟪 CRITICAL - Magenta

Format:
```
2024-01-15 10:30:00 | INFO     | myapp | Message | extra=value
```

## JSON Formatter

For production, logs are structured JSON:

```json
{
  "timestamp": "2024-01-15T10:30:00.123456+00:00",
  "level": "INFO",
  "logger": "myapp.views",
  "message": "Request processed",
  "request_id": "abc-123",
  "duration_ms": 45,
  "status_code": 200
}
```

## Programmatic Configuration

```python
from zeeb_api.logging import configure_logging

# Configure manually
configure_logging(
    level="DEBUG",
    json_logs=False,
    log_file="logs/debug.log",
    log_rotation=True,
    log_retention_days=7,
)
```

## Quick Functions

For simple logging without creating a logger:

```python
from zeeb_api.logging import debug, info, warning, error, critical

info("Server started", port=8000)
error("Connection failed", host="db.example.com")
```

## Structured Logging Best Practices

### 1. Use Extra Fields

```python
# Good - structured and searchable
logger.info("Order completed", order_id=123, total=99.99, items=3)

# Avoid - unstructured
logger.info(f"Order 123 completed for $99.99 with 3 items")
```

### 2. Consistent Field Names

```python
# Good - consistent naming
logger.info("User action", user_id=123, action="login")
logger.info("User action", user_id=123, action="logout")

# Avoid - inconsistent
logger.info("Login", uid=123)
logger.info("User logged out", user=123)
```

### 3. Include Context

```python
# Good - includes context for debugging
logger.error(
    "Payment failed",
    order_id=order.id,
    user_id=user.id,
    amount=payment.amount,
    provider=payment.provider,
    error_code=e.code,
)
```

### 4. Use Appropriate Levels

```python
# DEBUG - Detailed debugging (disabled in production)
logger.debug("Cache lookup", key="user:123", hit=True)

# INFO - Normal operations
logger.info("Order created", order_id=123)

# WARNING - Unexpected but handled
logger.warning("Rate limit approaching", user_id=123, requests=95)

# ERROR - Errors that need attention
logger.error("Payment failed", order_id=123, error="Card declined")

# CRITICAL - System failures
logger.critical("Database connection lost", host="db.example.com")
```

## Integration with uvicorn

Zeeb configures uvicorn logging automatically:

```python
# settings.py
LOGGING = {
    "level": "INFO",
    "json_logs": True,
}
```

uvicorn access logs follow the same format:

```json
{"timestamp": "...", "level": "INFO", "logger": "uvicorn.access", "message": "GET /api/users 200"}
```

## Middleware Logging

Log all requests automatically:

```python
# myproject/middleware.py
import time
from zeeb_api.logging import get_logger

logger = get_logger("requests")


async def logging_middleware(request, call_next):
    start = time.time()
    request_id = str(uuid.uuid4())[:8]
    
    logger.info(
        "Request started",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )
    
    response = await call_next(request)
    
    duration_ms = (time.time() - start) * 1000
    logger.info(
        "Request completed",
        request_id=request_id,
        status_code=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    
    return response
```

## Log Aggregation

JSON logs are easily parsed by log aggregation tools:

- **ELK Stack** (Elasticsearch, Logstash, Kibana)
- **Datadog**
- **Splunk**
- **CloudWatch Logs**
- **Loki + Grafana**

Example Filebeat configuration:

```yaml
filebeat.inputs:
  - type: log
    paths:
      - /var/log/myapp/*.log
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["elasticsearch:9200"]
  index: "myapp-logs-%{+yyyy.MM.dd}"
```

## Performance Tips

### 1. Use Lazy Formatting

```python
# Good - string formatting only if logged
logger.debug("Processing item %s of %s", current, total)

# Avoid - always formats string
logger.debug(f"Processing item {current} of {total}")
```

### 2. Check Level Before Expensive Operations

```python
if logger.isEnabledFor(logging.DEBUG):
    # Only compute if DEBUG is enabled
    details = expensive_debug_info()
    logger.debug("Debug info", details=details)
```

### 3. Async-Safe Logging

The logger is safe to use in async code:

```python
async def my_async_function():
    logger.info("Starting async operation")
    result = await some_async_call()
    logger.info("Completed", result=result)
```

## Next Steps

- [Settings](settings.md) - All configuration options
- [CLI Commands](../cli/commands.md) - Log-related commands
- [ViewSets](../api/viewsets.md) - Logging in views
