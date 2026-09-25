class ETLError(Exception):
    """Base exception for ETL-related errors."""


class ConfigurationError(ETLError):
    """Raised when configuration is missing or invalid."""


class DatabaseConnectionError(ETLError):
    """Raised when the SQL Server connection fails."""


class ExtractionError(ETLError):
    """Raised when data extraction fails."""


class ValidationError(ETLError):
    """Raised when validation fails."""


class FileGenerationError(ETLError):
    """Raised when file generation fails."""


class SFTPConnectionError(ETLError):
    """Raised when SFTP connection fails."""


class SFTPUploadError(ETLError):
    """Raised when file upload fails."""


class SFTPDownloadError(ETLError):
    """Raised when response file download fails."""