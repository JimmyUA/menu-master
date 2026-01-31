"""
Batch Job Entry Point

This script is the entry point for the Cloud Run Job.
It initializes the MenuGenerator and runs the batch process.
"""

import os
import logging
from menu_generator import MenuGenerator

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        logger.error("GOOGLE_CLOUD_PROJECT environment variable is required")
        return

    location = os.environ.get("VERTEX_AI_LOCATION", "us-central1")

    logger.info(f"Starting Menu Generation Job in project {project_id} ({location})")

    try:
        generator = MenuGenerator(project_id=project_id, location=location)
        generator.process_all_users()
    except Exception as e:
        logger.critical(f"Job failed with critical error: {e}")
        raise

if __name__ == "__main__":
    main()
