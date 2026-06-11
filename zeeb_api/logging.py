"""
Zeeb Logging Module.

Provides structured logging with JSON support, configurable levels,
and integration with FastAPI/uvicorn.

Usage:
    from zeeb_api.logging import get_logger, configure_logging
    
    # Get a logger
    logger = get_logger(__name__)
    logger.info("User logged in", user_id=123, ip="192.168.1.1")
    
    # Configure logging (typically in app startup)
    configure_logging(level="INFO", json_logs=True)
"""

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from typing import Any
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.
    
    Outputs logs as JSON objects with consistent fields:
    - timestamp: ISO 8601 timestamp
    - level: Log level (INFO, ERROR, etc.)
    - logger: Logger name
    - message: Log message
    - ...extra: Any additional fields passed to the log call
    """
    
    def __init__(
        self,
        include_timestamp: bool = True,
        include_level: bool = True,
        include_logger: bool = True,
        include_pathname: bool = False,
        include_lineno: bool = False,
        timestamp_format: str | None = None,
    ):
        super().__init__()
        self.include_timestamp = include_timestamp
        self.include_level = include_level
        self.include_logger = include_logger
        self.include_pathname = include_pathname
        self.include_lineno = include_lineno
        self.timestamp_format = timestamp_format
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: dict[str, Any] = {}
        
        # Timestamp
        if self.include_timestamp:
            if self.timestamp_format:
                log_data["timestamp"] = datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).strftime(self.timestamp_format)
            else:
                log_data["timestamp"] = datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).isoformat()
        
        # Level
        if self.include_level:
            log_data["level"] = record.levelname
        
        # Logger name
        if self.include_logger:
            log_data["logger"] = record.name
        
        # Message - safely get formatted message
        try:
            log_data["message"] = record.getMessage()
        # Broad except: guards %-style log message formatting against bad user-supplied args
        except Exception:
            log_data["message"] = str(record.msg)
        
        # Source location (optional)
        if self.include_pathname:
            log_data["pathname"] = record.pathname
        if self.include_lineno:
            log_data["lineno"] = record.lineno
        
        # Exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Extra fields (passed via extra={} or logger adapter)
        # Filter out internal/uvicorn fields
        extra_keys = set(record.__dict__.keys()) - {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'pathname', 'process', 'processName', 'relativeCreated',
            'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
            'message', 'taskName',
            # Uvicorn internal fields
            'color_message',
        }
        for key in extra_keys:
            log_data[key] = getattr(record, key)
        
        return json.dumps(log_data, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """
    Colored console formatter for development.
    
    Outputs human-readable logs with colors for different levels.
    """
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'
    
    def __init__(self, use_colors: bool = True):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        self.use_colors = use_colors
    
    def format(self, record: logging.LogRecord) -> str:
        """Format with optional colors."""
        # Get the formatted message first (handles %-style formatting)
        try:
            message = record.getMessage()
        # Broad except: guards %-style log message formatting against bad user-supplied args
        except Exception:
            message = str(record.msg)
        
        # Add extra fields to message (filter out internal/uvicorn fields)
        extra_keys = set(record.__dict__.keys()) - {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'pathname', 'process', 'processName', 'relativeCreated',
            'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
            'message', 'asctime', 'taskName',
            # Uvicorn internal fields
            'color_message',
        }
        
        if extra_keys:
            extras = " | ".join(f"{k}={getattr(record, k)}" for k in sorted(extra_keys))
            message = f"{message} | {extras}"
        
        # Create a copy of the record with the modified message
        record = logging.makeLogRecord(record.__dict__)
        record.msg = message
        record.args = ()
        
        formatted = super().format(record)
        
        if self.use_colors and record.levelname in self.COLORS:
            color = self.COLORS[record.levelname]
            formatted = f"{color}{formatted}{self.RESET}"
        
        return formatted


class ZeebLogger(logging.LoggerAdapter):
    """
    Logger adapter that makes it easy to pass extra fields.
    
    Usage:
        logger = get_logger(__name__)
        logger.info("Request processed", user_id=123, duration_ms=45)
    """
    
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Process log call to handle extra fields."""
        # Extract extra fields from kwargs
        extra = kwargs.get('extra', {})
        
        # Move non-standard kwargs to extra
        standard_kwargs = {'exc_info', 'stack_info', 'stacklevel', 'extra'}
        for key in list(kwargs.keys()):
            if key not in standard_kwargs:
                extra[key] = kwargs.pop(key)
        
        # Merge with adapter's extra
        if self.extra:
            extra = {**self.extra, **extra}
        
        kwargs['extra'] = extra
        return msg, kwargs


# Global logging configuration
_logging_configured = False
_log_level = logging.INFO
_json_logs = False
_log_file: str | None = None


def configure_logging(
    level: str | int = "INFO",
    json_logs: bool = False,
    log_file: str | None = None,
    log_rotation: bool = True,
    log_retention_days: int = 30,
    include_uvicorn: bool = True,
    include_sqlalchemy: bool = False,
) -> None:
    """
    Configure logging for the application.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_logs: If True, output logs as JSON (recommended for production)
        log_file: Optional file path to write logs to
        log_rotation: If True, rotate logs at midnight (default: True)
        log_retention_days: Number of days to keep old log files (default: 30)
        include_uvicorn: Configure uvicorn loggers too
        include_sqlalchemy: Include SQLAlchemy engine logs (verbose)
    
    Example:
        # Development
        configure_logging(level="DEBUG", json_logs=False)
        
        # Production with rotation
        configure_logging(
            level="INFO", 
            json_logs=True, 
            log_file="logs/app.log",
            log_rotation=True,
            log_retention_days=30
        )
    """
    global _logging_configured, _log_level, _json_logs, _log_file
    
    # Parse level
    if isinstance(level, str):
        _log_level = getattr(logging, level.upper(), logging.INFO)
    else:
        _log_level = level
    
    _json_logs = json_logs
    _log_file = log_file
    
    # Create handlers
    handlers: list[logging.Handler] = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        console_handler.setFormatter(JsonFormatter())
    else:
        console_handler.setFormatter(ConsoleFormatter())
    console_handler.setLevel(_log_level)
    handlers.append(console_handler)
    
    # File handler with rotation (always JSON for easier parsing)
    if log_file:
        # Auto-create logs directory
        log_path = Path(log_file)
        if log_path.parent and not log_path.parent.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
        
        if log_rotation:
            # TimedRotatingFileHandler - rotates at midnight
            file_handler = logging.handlers.TimedRotatingFileHandler(
                log_file,
                when="midnight",
                interval=1,
                backupCount=log_retention_days,
                encoding="utf-8",
            )
            # Add date suffix to rotated files (e.g., app.log.2026-02-06)
            file_handler.suffix = "%Y-%m-%d"
        else:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
        
        file_handler.setFormatter(JsonFormatter())
        file_handler.setLevel(_log_level)
        handlers.append(file_handler)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(_log_level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add new handlers
    for handler in handlers:
        root_logger.addHandler(handler)
    
    # Configure zeeb loggers
    logging.getLogger("zeeb").setLevel(_log_level)
    logging.getLogger("zeeb_orm").setLevel(_log_level)
    logging.getLogger("zeeb_api").setLevel(_log_level)
    
    # Configure uvicorn loggers
    if include_uvicorn:
        for name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = []
            uvicorn_logger.propagate = True
    
    # Configure SQLAlchemy logger
    if include_sqlalchemy:
        logging.getLogger("sqlalchemy.engine").setLevel(_log_level)
    else:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    
    _logging_configured = True


def get_logger(name: str | None = None, **extra: Any) -> ZeebLogger:
    """
    Get a logger instance.
    
    Args:
        name: Logger name (typically __name__)
        **extra: Default extra fields to include in all log messages
    
    Returns:
        ZeebLogger instance
    
    Example:
        logger = get_logger(__name__)
        logger.info("Starting server", port=8000)
        
        # With default extra fields
        logger = get_logger(__name__, service="auth")
        logger.info("User login")  # Includes service="auth"
    """
    # Auto-configure if not done
    if not _logging_configured:
        configure_logging()
    
    base_logger = logging.getLogger(name or "zeeb")
    return ZeebLogger(base_logger, extra or {})


def get_request_logger(request_id: str, **extra: Any) -> ZeebLogger:
    """
    Get a logger with request context.
    
    Useful for tracing logs across a single request.
    
    Args:
        request_id: Unique request identifier
        **extra: Additional context fields
    
    Example:
        logger = get_request_logger("req-123", user_id=456)
        logger.info("Processing request")
    """
    return get_logger("zeeb.request", request_id=request_id, **extra)


# Convenience functions for quick logging
def debug(msg: str, **kwargs: Any) -> None:
    """Log debug message."""
    get_logger().debug(msg, **kwargs)


def info(msg: str, **kwargs: Any) -> None:
    """Log info message."""
    get_logger().info(msg, **kwargs)


def warning(msg: str, **kwargs: Any) -> None:
    """Log warning message."""
    get_logger().warning(msg, **kwargs)


def error(msg: str, **kwargs: Any) -> None:
    """Log error message."""
    get_logger().error(msg, **kwargs)


def critical(msg: str, **kwargs: Any) -> None:
    """Log critical message."""
    get_logger().critical(msg, **kwargs)


def exception(msg: str, **kwargs: Any) -> None:
    """Log exception with traceback."""
    get_logger().exception(msg, **kwargs)


def configure_logging_from_settings() -> None:
    """
    Configure logging from project settings.
    
    Reads the LOGGING dict from settings.py and applies the configuration.
    Call this during app startup.
    
    Example settings.py:
        LOGGING = {
            "level": "INFO",
            "json_logs": True,
            "log_file": "logs/app.log",
        }
    """
    import sys
    from pathlib import Path
    
    # Try to find and load settings
    current = Path.cwd()
    while current != current.parent:
        if (current / "manage.py").exists():
            break
        current = current.parent
    else:
        # No project found, use defaults
        configure_logging()
        return
    
    # Add to path
    if str(current) not in sys.path:
        sys.path.insert(0, str(current))
    
    # Find settings module
    for item in current.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "settings", item / "settings.py"
                )
                if spec and spec.loader:
                    settings = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings)
                    
                    # Get LOGGING config
                    logging_config = getattr(settings, "LOGGING", {})
                    configure_logging(
                        level=logging_config.get("level", "INFO"),
                        json_logs=logging_config.get("json_logs", False),
                        log_file=logging_config.get("log_file"),
                    )
                    return
            except Exception as exc:
                # Logging is not configured yet, so report directly to stderr
                # before falling back to the default configuration.
                sys.stderr.write(
                    f"zeeb_api.logging: failed to load settings from "
                    f"{item / 'settings.py'}: {exc}; using default logging config\n"
                )

    # Fallback to defaults
    configure_logging()


__all__ = [
    # Main functions
    "configure_logging",
    "configure_logging_from_settings",
    "get_logger",
    "get_request_logger",
    # Formatters
    "JsonFormatter",
    "ConsoleFormatter",
    "ZeebLogger",
    # Quick logging
    "debug",
    "info", 
    "warning",
    "error",
    "critical",
    "exception",
]
