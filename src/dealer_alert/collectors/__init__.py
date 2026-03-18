"""Data collectors — email inbox polling, social media, and auto-subscription."""

from .auto_subscriber import AutoSubscriber
from .email_collector import EmailCollector
from .social_monitor import SocialMonitor

__all__ = ["AutoSubscriber", "EmailCollector", "SocialMonitor"]
