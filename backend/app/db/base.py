"""Import every model so Base.metadata is complete (Alembic autogenerate, test schema creation)."""

from app.core.db import Base  # noqa: F401
from app.modules.collections import models as collections_models  # noqa: F401
from app.modules.dispatch import models as dispatch_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.integrations.tally import models as tally_models  # noqa: F401
from app.modules.masters import models as masters_models  # noqa: F401
from app.modules.orders import models as orders_models  # noqa: F401
from app.modules.platform import models as platform_models  # noqa: F401
from app.modules.purchases import models as purchases_models  # noqa: F401
from app.modules.wa_orders import models as wa_orders_models  # noqa: F401
