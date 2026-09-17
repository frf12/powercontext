# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LearningRun side table factory; Artifact payloads remain in the shared tables."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    Table,
    UniqueConstraint,
)

from powercontext.limits import MAX_SCOPE_ID_LENGTH


def create_trace_learning_runs_table(metadata, identity_string, payload_type) -> Table:
    return Table(
        "pc_trace_learning_runs",
        metadata,
        Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
        Column("run_id", identity_string(64), primary_key=True),
        Column("principal_key", identity_string(64), nullable=False),
        Column("idempotency_key", identity_string(128), nullable=False),
        Column("request_digest", identity_string(71), nullable=False),
        Column("status", identity_string(16), nullable=False),
        Column("accepted_at", BigInteger, nullable=False),
        Column("generation", Integer, nullable=False),
        Column("request_generation", BigInteger, nullable=False),
        Column("payload", payload_type, nullable=False),
        ForeignKeyConstraint(("scope_id",), ("pc_scopes.scope_id",), ondelete="CASCADE"),
        UniqueConstraint("scope_id", "principal_key", "idempotency_key", name="uq_pc_learning_idempotency"),
        Index("ix_pc_learning_dispatch", "scope_id", "status", "request_generation"),
        Index("ix_pc_learning_completed", "scope_id", "status", "accepted_at"),
        CheckConstraint("generation >= 0", name="ck_pc_learning_generation"),
    )
