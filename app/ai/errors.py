"""Shared error type for AI-backed analysis, transcription, and report writing."""


class ReportAIError(RuntimeError):
    """Raised when an AI provider cannot transcribe audio or write a valid report."""
