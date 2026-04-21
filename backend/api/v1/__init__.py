"""Flask API v1 blueprint registration.

All v1 route modules register their routes on the api_v1 blueprint.
Import order matters — modules are imported after the blueprint is
created so they can use @api_v1.route decorators.
"""

from flask import Blueprint

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Import route modules to register their routes on the blueprint.
# These imports must come after api_v1 is defined.
from backend.api.v1 import admin as _admin  # noqa: F401, E402
from backend.api.v1 import apple_maps as _apple_maps  # noqa: F401, E402
from backend.api.v1 import auth as _auth  # noqa: F401, E402
from backend.api.v1 import auth_apple_music as _auth_apple_music  # noqa: F401, E402
from backend.api.v1 import auth_identity as _auth_identity  # noqa: F401, E402
from backend.api.v1 import auth_session as _auth_session  # noqa: F401, E402
from backend.api.v1 import auth_tidal as _auth_tidal  # noqa: F401, E402
from backend.api.v1 import cities as _cities  # noqa: F401, E402
from backend.api.v1 import events as _events  # noqa: F401, E402
from backend.api.v1 import onboarding as _onboarding  # noqa: F401, E402
from backend.api.v1 import recommendations as _recommendations  # noqa: F401, E402
from backend.api.v1 import saved_events as _saved_events  # noqa: F401, E402
from backend.api.v1 import users as _users  # noqa: F401, E402
from backend.api.v1 import venue_comments as _venue_comments  # noqa: F401, E402
from backend.api.v1 import venues as _venues  # noqa: F401, E402
