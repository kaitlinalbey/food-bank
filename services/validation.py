"""Validation helpers and geographic distance."""
from datetime import date
import math

class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def whole(value, minimum=0):
    if isinstance(value, bool) or value is None or not str(value).strip().isdecimal():
        raise Problem('Enter a whole number.')
    value = int(value)
    if value < minimum or value > 1000000:
        raise Problem(f'Number must be between {minimum} and 1000000.')
    return value


def clean_text(value, label, maximum=80):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise Problem(f'{label} must be 1 to {maximum} characters.')
    return value.strip()


def expiration(value):
    if value is None or value == '':
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise Problem('Expiration date must be YYYY-MM-DD.')


def good_date(date_string):
    return not date_string or date_string >= date.today().isoformat()


def haversine(lat1, lon1, lat2, lon2):
    r = 3958.8
    a1, a2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = a2-a1, math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2+math.cos(a1)*math.cos(a2)*math.sin(dlon/2)**2
    return round(2*r*math.asin(min(1,math.sqrt(a))),1)


