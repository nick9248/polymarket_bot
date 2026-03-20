import os
import requests
import logging
from dotenv import load_dotenv
from utility.constants import REQUEST_TIMEOUT_SECONDS

load_dotenv()
logger = logging.getLogger(__name__)

def is_in_spain() -> bool:
    """
    Checks if the system's current public IP is located in Spain.
    If CHECK_GEO_IP is not exactly 'True', it bypasses the check.
    
    Returns:
        True if check is bypassed or if the IP is in Spain ('ES').
        False otherwise.
    """
    check_geo = os.getenv("CHECK_GEO_IP", "False")
    
    if check_geo.lower() != "true":
        logger.info("Geo IP check is disabled via CHECK_GEO_IP (.env). Bypassing.")
        return True
        
    logger.info("Geo IP check is ENABLED. Fetching IP location...")
    
    try:
        resp = requests.get("https://ipinfo.io/json", timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        
        country = data.get("country", "")
        ip_addr = data.get("ip", "unknown")
        
        logger.info(f"Detected IP: {ip_addr}, Country: {country}")
        
        if country == "ES":
            logger.info("Geo validation passed: System is in Spain.")
            return True
        else:
            logger.error(f"Geo validation failed: System is in {country}, not ES (Spain).")
            return False
            
    except Exception as e:
        logger.error(f"Failed to fetch IP geolocation: {e}")
        return False
