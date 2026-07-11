"""
Minimal analytics hook. Replace `track` with a real pipeline
(Segment, PostHog, Amplitude, ...) when available.

Events used by the waitlist feature:
waitlist_joined, offer_sent, offer_accepted, offer_expired,
offer_declined, slot_filled
"""

import logging

logger = logging.getLogger("sanctuary.analytics")


def track(event: str, **properties):
    logger.info("[ANALYTICS] %s %s", event, properties)
