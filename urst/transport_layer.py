import logging

logger = logging.getLogger(__name__)


class TransportLayer:
    """
    Handles the physical transport of URST packets.
    """

    def __init__(self):
        logger.debug("Initializing Transport Layer")
