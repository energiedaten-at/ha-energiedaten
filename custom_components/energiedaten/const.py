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

# Options flow toggle for the community-share sensors.
CONF_COMMUNITY_SHARE: Final = "community_share"

# The community-share leg of each direction's OBIS triplet: energy drawn from
# (G.03) or fed into (X.01) the meter's energy community. The API leaves both
# out unless asked for, since they read 0 for every meter outside a community.
COMMUNITY_SHARE_OBIS_CODES: Final = ("1-1:2.9.0 G.03", "1-1:2.9.0 X.01")

# Naming codes with `obis_codes[]` replaces the API's default vocabulary rather
# than adding to it, so the opt-in request has to repeat the defaults.
DEFAULT_OBIS_CODES: Final = (
    "1-1:1.9.0 P.01",
    "1-1:1.9.0 G.01",
    "1-1:2.9.0 P.01",
    "1-1:2.9.0 G.01",
    "7-1:1.9.0 P.01",
)
