"""Flask API v1 blueprint registration.

All v1 route modules register their routes on the api_v1 blueprint.
Import order matters — modules are imported after the blueprint is
created so they can use @api_v1.route decorators.
"""

from flask import Blueprint

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Import route modules to register their routes on the blueprint.
# These imports must come after api_v1 is defined.
from backend.api.v1 import cities as _cities  # noqa: F401, E402
from backend.api.v1 import events as _events  # noqa: F401, E402
from backend.api.v1 import venues as _venues  # noqa: F401, E402
