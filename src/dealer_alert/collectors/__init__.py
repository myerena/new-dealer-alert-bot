"""Data collectors — email inbox polling and social media monitoring."""

from .email_collector import EmailCollector
from .social_monitor import SocialMonitor

__all__ = ["EmailCollector", "SocialMonitor"]
