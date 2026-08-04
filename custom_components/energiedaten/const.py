"""Constants for the energiedaten.at integration."""

from typing import Final

DOMAIN: Final = "energiedaten"

CONF_TOKEN: Final = "token"
CONF_METERS: Final = "meters"
CONF_CURSORS: Final = "cursors"

# OBIS codes seen for each meter, so the sensor set survives a poll that
# returns nothing. A missing meter key means "not discovered yet", which is
# not the same as a meter known to report no OBIS code at all.
CONF_OBIS_CODES: Final = "obis_codes"
