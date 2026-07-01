"""Create the initial Hugo schema."""

from hugo import models  # noqa: F401
from hugo.db import Base

from alembic import op

revision = "20260629_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
