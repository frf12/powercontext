"""
OceanBase storage implementation

This module provides OceanBase-based storage for memory data.
"""
import heapq
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from mem.storage.base import VectorStoreBase, OutputData

try:
    from pyobvector import (
        VECTOR,
        ObVecClient,
        cosine_distance,
        inner_product,
        l2_distance,
        VecIndexType,
    )
    from sqlalchemy import JSON, Column, String, Table, func, ColumnElement
    from sqlalchemy import text, and_, or_, not_, select, bindparam, literal_column
    from sqlalchemy.dialects.mysql import LONGTEXT
except ImportError as e:
    raise ImportError(
        f"Required dependencies not found: {e}. Please install pyobvector and sqlalchemy."
    )

logger = logging.getLogger(__name__)

DEFAULT_OCEANBASE_CONNECTION = {
    "host": "localhost",
    "port": "2881",
    "user": "root@test",
    "password": "",
    "db_name": "test",
}

# Default parameters for different index types
DEFAULT_OCEANBASE_HNSW_BUILD_PARAM = {"M": 16, "efConstruction": 200}
DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM = {"efSearch": 64}
DEFAULT_OCEANBASE_IVF_BUILD_PARAM = {"nlist": 128}
DEFAULT_OCEANBASE_IVF_SEARCH_PARAM = {}
DEFAULT_OCEANBASE_FLAT_BUILD_PARAM = {}
DEFAULT_OCEANBASE_FLAT_SEARCH_PARAM = {}

# Supported index types mapping
OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES = {
    "HNSW": VecIndexType.HNSW,
    "HNSW_SQ": VecIndexType.HNSW_SQ,
    "IVF": VecIndexType.IVFFLAT,
    "IVF_FLAT": VecIndexType.IVFFLAT,
    "IVF_SQ": VecIndexType.IVFSQ,
    "IVF_PQ": VecIndexType.IVFPQ,
    "FLAT": VecIndexType.IVFFLAT,
}

DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE = "l2"
DEFAULT_METADATA_FIELD = "metadata"

# Supported fulltext parsers
OCEANBASE_SUPPORTED_FULLTEXT_PARSERS = ["ik", "ngram", "ngram2", "beng", "space"]

class OceanBaseVectorStore(VectorStoreBase):
    """OceanBase vector store implementation for mem0."""

    def __init__(
            self,
            collection_name: str,
            connection_args: Optional[Dict[str, Any]] = None,
            vidx_metric_type: str = DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE,
            vidx_algo_params: Optional[Dict] = None,
            index_type: str = "HNSW",
            embedding_model_dims: Optional[int] = None,
            primary_field: str = "id",
            vector_field: str = "embedding",
            text_field: str = "document",
            metadata_field: str = DEFAULT_METADATA_FIELD,
            vidx_name: str = "vidx",
            normalize: bool = False,
            include_sparse: bool = False,
            auto_configure_vector_index: bool = True,
            # Connection parameters (for compatibility with config)
            host: Optional[str] = None,
            port: Optional[str] = None,
            user: Optional[str] = None,
            password: Optional[str] = None,
            db_name: Optional[str] = None,
            hybrid_search: bool = True,
            fulltext_parser: str = "ik",
            **kwargs,
    ):
        """
        Initialize the OceanBase vector store.

        Args:
            collection_name (str): Name of the collection/table.
            connection_args (Optional[Dict[str, Any]]): Connection parameters for OceanBase.
            vidx_metric_type (str): Metric method of distance between vectors.
            vidx_algo_params (Optional[Dict]): Index parameters.
            index_type (str): Type of vector index to use.
            embedding_model_dims (Optional[int]): Dimension of vectors.
            primary_field (str): Name of the primary key column.
            vector_field (str): Name of the vector column.
            text_field (str): Name of the text column.
            metadata_field (str): Name of the metadata column.
            vidx_name (str): Name of the vector index.
            normalize (bool): Whether to perform L2 normalization on vectors.
            include_sparse (bool): Whether to include sparse vector support.
            auto_configure_vector_index (bool): Whether to automatically configure vector index settings.
            host (Optional[str]): OceanBase server host.
            port (Optional[str]): OceanBase server port.
            user (Optional[str]): OceanBase username.
            password (Optional[str]): OceanBase password.
            db_name (Optional[str]): OceanBase database name.
            hybrid_search (bool): Whether to use hybrid search.
        """
        self.normalize = normalize
        self.include_sparse = include_sparse
        self.auto_configure_vector_index = auto_configure_vector_index
        self.hybrid_search = hybrid_search
        self.fulltext_parser = fulltext_parser

        # Validate fulltext parser
        if self.fulltext_parser not in OCEANBASE_SUPPORTED_FULLTEXT_PARSERS:
            supported = ', '.join(OCEANBASE_SUPPORTED_FULLTEXT_PARSERS)
            raise ValueError(
                f"Invalid fulltext parser: {self.fulltext_parser}. "
                f"Supported parsers are: {supported}"
            )

        # Handle connection arguments - prioritize individual parameters over connection_args
        if connection_args is None:
            connection_args = {}

        # Merge individual connection parameters with connection_args
        final_connection_args = {
            "host": host or connection_args.get("host", DEFAULT_OCEANBASE_CONNECTION["host"]),
            "port": port or connection_args.get("port", DEFAULT_OCEANBASE_CONNECTION["port"]),
            "user": user or connection_args.get("user", DEFAULT_OCEANBASE_CONNECTION["user"]),
            "password": password or connection_args.get("password", DEFAULT_OCEANBASE_CONNECTION["password"]),
            "db_name": db_name or connection_args.get("db_name", DEFAULT_OCEANBASE_CONNECTION["db_name"]),
        }

        self.connection_args = final_connection_args

        self.index_type = index_type.upper()
        if self.index_type not in OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES:
            raise ValueError(
                f"`index_type` should be one of "
                f"{list(OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES.keys())}. "
                f"Got {self.index_type}"
            )

        # Set default parameters based on index type
        if vidx_algo_params is None:
            index_param_map = {
                "HNSW": DEFAULT_OCEANBASE_HNSW_BUILD_PARAM,
                "HNSW_SQ": DEFAULT_OCEANBASE_HNSW_BUILD_PARAM,
                "IVF": DEFAULT_OCEANBASE_IVF_BUILD_PARAM,
                "IVF_FLAT": DEFAULT_OCEANBASE_IVF_BUILD_PARAM,
                "IVF_SQ": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
                "IVF_PQ": DEFAULT_OCEANBASE_IVF_BUILD_PARAM,
                "FLAT": DEFAULT_OCEANBASE_FLAT_BUILD_PARAM,
            }
            self.vidx_algo_params = index_param_map[self.index_type].copy()

            if self.index_type == "IVF_PQ" and "m" not in self.vidx_algo_params:
                self.vidx_algo_params["m"] = 3
        else:
            self.vidx_algo_params = vidx_algo_params.copy()

        # Set field names
        self.collection_name = collection_name
        self.embedding_model_dims = embedding_model_dims
        self.primary_field = primary_field
        self.vector_field = vector_field
        self.text_field = text_field
        self.metadata_field = metadata_field
        self.vidx_name = vidx_name
        self.sparse_vector_field = "sparse_embedding"
        self.fulltext_field = "fulltext_content"

        # Set up vector index parameters
        self.vidx_metric_type = vidx_metric_type.lower()

        # Initialize client
        self._create_client(**kwargs)
        assert self.obvector is not None

        # Autoconfigure vector index settings if enabled
        if self.auto_configure_vector_index:
            self._configure_vector_index_settings()

        self._create_col()

    def _create_client(self, **kwargs):
        """Create and initialize the OceanBase vector client."""
        host = self.connection_args.get("host")
        port = self.connection_args.get("port")
        user = self.connection_args.get("user")
        password = self.connection_args.get("password")
        db_name = self.connection_args.get("db_name")

        self.obvector = ObVecClient(
            uri=f"{host}:{port}",
            user=user,
            password=password,
            db_name=db_name,
            **kwargs,
        )

    def _configure_vector_index_settings(self):
        """Configure OceanBase vector index settings automatically."""
        try:
            logger.info("Configuring OceanBase vector index settings...")

            # Set vector memory limit percentage
            with self.obvector.engine.connect() as conn:
                conn.execute(text("ALTER SYSTEM SET ob_vector_memory_limit_percentage = 30"))
                conn.commit()
                logger.info("Set ob_vector_memory_limit_percentage = 30")

            logger.info("OceanBase vector index configuration completed")

        except Exception as e:
            logger.warning(f"Failed to configure vector index settings: {e}")
            logger.warning("   Vector index functionality may not work properly")

    def _create_table_with_index_by_embedding_model_dims(self) -> None:
        """Create table with vector index based on embedding dimension."""
        # Create columns following mem0 standard schema
        cols = [
            # Primary key
            Column(
                self.primary_field, String(64), primary_key=True, autoincrement=False
            ),
            # Vector field
            Column(self.vector_field, VECTOR(self.embedding_model_dims)),
            # Text content field
            Column(self.text_field, LONGTEXT),
            # Metadata field (JSON)
            Column(self.metadata_field, JSON),
            # mem0 standard fields for filtering
            Column("user_id", String(128)),  # User identifier
            Column("agent_id", String(128)),  # Agent identifier
            Column("run_id", String(128)),  # Run identifier
            Column("actor_id", String(128)),  # Actor identifier
            Column("hash", String(32)),  # MD5 hash (32 chars)
            Column("created_at", String(128)),
            Column("updated_at", String(128)),
            Column("category", String(64)),  # Category name
            Column(self.fulltext_field, LONGTEXT)
        ]

        # Add hybrid search columns if enabled
        if self.include_sparse:
            cols.append(Column(self.sparse_vector_field, JSON))

        # Create vector index
        vidx_params = self.obvector.prepare_index_params()
        vidx_params.add_index(
            field_name=self.vector_field,
            index_type=OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES[self.index_type],
            index_name=self.vidx_name,
            metric_type=self.vidx_metric_type,
            params=self.vidx_algo_params,
        )

        # Add sparse vector index if enabled
        if self.include_sparse:
            logger.warning("Sparse vector indexing not fully implemented yet")

        # Create table with vector index first
        self.obvector.create_table_with_index_params(
            table_name=self.collection_name,
            columns=cols,
            indexes=None,
            vidxs=vidx_params,
            partitions=None,
        )

        logger.debug("DEBUG: Table '%s' created successfully", self.collection_name)

    def _normalize(self, vector: List[float]) -> List[float]:
        """Normalize vector using L2 normalization."""
        import numpy as np
        arr = np.array(vector)
        norm = np.linalg.norm(arr)
        if norm == 0:
            return vector
        arr = arr / norm
        return arr.tolist()

    def _get_distance_function(self, metric_type: str):
        """Get the appropriate distance function for the given metric type."""
        if metric_type == "inner_product":
            return inner_product
        elif metric_type == "l2":
            return l2_distance
        elif metric_type == "cosine":
            return cosine_distance
        else:
            raise ValueError(f"Unsupported metric type: {metric_type}")

    def _get_default_search_params(self) -> dict:
        """Get default search parameters based on index type."""
        search_param_map = {
            "HNSW": DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM,
            "HNSW_SQ": DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM,
            "IVF": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
            "IVF_FLAT": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
            "IVF_SQ": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
            "IVF_PQ": DEFAULT_OCEANBASE_IVF_SEARCH_PARAM,
            "FLAT": DEFAULT_OCEANBASE_FLAT_SEARCH_PARAM,
        }
        return search_param_map.get(
            self.index_type, DEFAULT_OCEANBASE_HNSW_SEARCH_PARAM
        )

    def create_col(self, name: str, vector_size: Optional[int] = None, distance: str = "l2"):
        """Create a new collection."""
        if vector_size is None:
            raise ValueError("vector_size must be specified to create a collection.")
        distance = distance.lower()
        if distance not in ("l2", "inner_product", "cosine"):
            raise ValueError("distance must be one of 'l2', 'inner_product', or 'cosine'.")
        self.embedding_model_dims = vector_size
        self.vidx_metric_type = distance
        self.collection_name = name

        self._create_col()

    def _create_col(self):
        """Create a new collection."""

        if self.embedding_model_dims is None:
            raise ValueError(
                "embedding_model_dims is required for OceanBase vector operations. "
                "Please configure embedding_model_dims in your OceanBaseConfig."
            )

        # Set up vector index parameters
        if self.vidx_metric_type not in ("l2", "inner_product", "cosine"):
            raise ValueError(
                "`vidx_metric_type` should be set in `l2`/`inner_product`/`cosine`."
            )

        # Only create table if it doesn't exist (preserve existing data)
        if not self.obvector.check_table_exists(self.collection_name):
            self._create_table_with_index_by_embedding_model_dims()
            logger.info(f"Created new table {self.collection_name}")
        else:
            logger.info(f"Table {self.collection_name} already exists, preserving existing data")
            # Check if the existing table's vector dimension matches the requested dimension
            existing_dim = self._get_existing_vector_dimension()
            if existing_dim is not None and existing_dim != self.embedding_model_dims:
                raise ValueError(
                    f"Vector dimension mismatch: existing table '{self.collection_name}' has "
                    f"vector dimension {existing_dim}, but requested dimension is {self.embedding_model_dims}. "
                    f"Please use a different collection name or delete the existing table."
                )

        if self.hybrid_search:
            self._check_and_create_fulltext_index()
        self.table = Table(self.collection_name, self.obvector.metadata_obj, autoload_with=self.obvector.engine)

    def insert(self,
               vectors: List[List[float]],
               payloads: Optional[List[Dict]] = None,
               ids: Optional[List[str]] = None):
        """Insert vectors into the collection."""
        if not vectors:
            return []

        if ids is None:
            ids = [str(uuid.uuid4()) for _ in vectors]

        if payloads is None:
            payloads = [{} for _ in vectors]

        # Prepare data for insertion
        data: List[Dict[str, Any]] = []
        for vector, payload, vector_id in zip(vectors, payloads, ids):
            record = self._build_record_for_insert(vector_id, vector, payload)
            data.append(record)

        # Insert data
        self.obvector.upsert(
            table_name=self.collection_name,
            data=data,
        )

        return ids

    def _parse_metadata(self, metadata_json):
        """Parse metadata from OceanBase, handling potential double encoding."""
        if isinstance(metadata_json, str):
            try:
                # First attempt to parse
                metadata = json.loads(metadata_json)
                # Check if it's still a string (double encoded)
                if isinstance(metadata, str):
                    try:
                        # Second attempt to parse
                        metadata = json.loads(metadata)
                    except json.JSONDecodeError:
                        metadata = {}
                return metadata
            except json.JSONDecodeError:
                return {}
        elif isinstance(metadata_json, dict):
            return metadata_json
        else:
            return {}

    def _generate_where_clause(self, filters: Optional[Dict] = None) -> Optional[List]:
        """
        Generate a properly formatted where clause for OceanBase.

        Args:
            filters (Optional[Dict]): The filter conditions.
                Supports both simple and complex formats:

                Simple format (Open Source):
                - Simple values: {"field": "value"} -> field = 'value'
                - Comparison ops: {"field": {"gte": 10, "lte": 20}}
                - List values: {"field": ["a", "b", "c"]} -> field IN ('a', 'b', 'c')

                Complex format (Platform):
                - AND logic: {"AND": [{"user_id": "alice"}, {"category": "food"}]}
                - OR logic: {"OR": [{"rating": {"gte": 4.0}}, {"priority": "high"}]}
                - Nested: {"AND": [{"user_id": "alice"}, {"OR": [{"rating": {"gte": 4.0}}, {"priority": "high"}]}]}

        Returns:
            Optional[List]: List of SQLAlchemy ColumnElement objects for where clause.
        """

        def get_column(key) -> ColumnElement:
            """Get the appropriate column element for a field."""
            if key in self.table.c:
                return self.table.c[key]
            else:
                # Use ->> operator for unquoted JSON extract (MySQL/PostgreSQL)
                return self.table.c[self.metadata_field].op("->>")(f"$.{key}")

        def build_condition(key, value):
            """Build a single condition."""
            column = get_column(key)

            if isinstance(value, dict):
                # Handle comparison operators
                conditions = []
                for op, op_value in value.items():
                    op = op.lstrip("$")
                    match op:
                        case "eq":
                            conditions.append(column == op_value)
                        case "ne":
                            conditions.append(column != op_value)
                        case "gt":
                            conditions.append(column > op_value)
                        case "gte":
                            conditions.append(column >= op_value)
                        case "lt":
                            conditions.append(column < op_value)
                        case "lte":
                            conditions.append(column <= op_value)
                        case "in":
                            if not isinstance(op_value, list):
                                raise TypeError(f"Value for $in must be a list, got {type(op_value)}")
                            conditions.append(column.in_(op_value))
                        case "nin":
                            if not isinstance(op_value, list):
                                raise TypeError(f"Value for $nin must be a list, got {type(op_value)}")
                            conditions.append(~column.in_(op_value))
                        case "like":
                            conditions.append(column.like(str(op_value)))
                        case "ilike":
                            conditions.append(column.ilike(str(op_value)))
                        case _:
                            raise ValueError(f"Unsupported operator: {op}")
                return and_(*conditions) if conditions else None
            elif value is None:
                return column.is_(None)
            else:
                return column == value

        def process_condition(cond):
            """Process a single condition, handling nested AND/OR logic."""
            if isinstance(cond, dict):
                # Handle complex filters with AND/OR
                if "AND" in cond:
                    and_conditions = [process_condition(item) for item in cond["AND"]]
                    and_conditions = [c for c in and_conditions if c is not None]
                    return and_(*and_conditions) if and_conditions else None
                elif "OR" in cond:
                    or_conditions = [process_condition(item) for item in cond["OR"]]
                    or_conditions = [c for c in or_conditions if c is not None]
                    return or_(*or_conditions) if or_conditions else None
                else:
                    # Simple key-value filters
                    conditions = []
                    for k, v in cond.items():
                        expr = build_condition(k, v)
                        if expr is not None:
                            conditions.append(expr)
                    return and_(*conditions) if conditions else None
            elif isinstance(cond, list):
                subconditions = [process_condition(c) for c in cond]
                subconditions = [c for c in subconditions if c is not None]
                return and_(*subconditions) if subconditions else None
            else:
                return None

        # Handle complex filters with AND/OR
        result = process_condition(filters)
        return [result] if result is not None else None

    def _parse_row(self, row) -> tuple:
        """Parse a database result row. Returns up to 12 fields, padding with None if needed."""
        padded_row = list(row) + [None] * (12 - len(row))
        return tuple(padded_row[:12])

    def _build_standard_metadata(self, user_id: str, agent_id: str, run_id: str,
                                 actor_id: str, hash_val: str, created_at: str,
                                 updated_at: str, category: str, metadata_json: str) -> Dict:
        """Build standard metadata dictionary from row fields."""
        # Parse the JSON metadata first
        metadata = self._parse_metadata(metadata_json)

        # Add mem0 standard fields
        metadata.update({
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "actor_id": actor_id,
            "hash": hash_val,
            "created_at": created_at,
            "updated_at": updated_at,
            "category": category,
        })

        return metadata

    def _create_output_data(self, vector_id: str, text_content: str, score: float,
                            metadata: Dict) -> OutputData:
        """Create an OutputData object with standard structure."""
        return OutputData(
            id=vector_id,
            score=score,
            payload={
                "data": text_content,
                **metadata
            }
        )

    def _build_record_for_insert(self, vector_id: str, vector: List[float], payload: Dict) -> Dict[str, Any]:
        """Build a record dictionary for insertion with all standard fields."""
        record = {
            self.primary_field: vector_id,
            self.vector_field: (
                vector if not self.normalize else self._normalize(vector)
            ),
            self.text_field: payload.get("data", ""),
            self.metadata_field: json.dumps(payload),
            # mem0 standard fields
            "user_id": payload.get("user_id", ""),
            "agent_id": payload.get("agent_id", ""),
            "run_id": payload.get("run_id", ""),
            "actor_id": payload.get("actor_id", ""),
            "hash": payload.get("hash", ""),
            "created_at": payload.get("created_at", ""),
            "updated_at": payload.get("updated_at", ""),
            "category": payload.get("category", ""),
        }

        # Add hybrid search fields if enabled
        if self.include_sparse and "sparse_embedding" in payload:
            record[self.sparse_vector_field] = json.dumps(payload["sparse_embedding"])

        # Always add full-text content (enabled by default)
        fulltext_content = payload.get("fulltext_content") or payload.get("data", "")
        record[self.fulltext_field] = fulltext_content

        return record

    def search(self,
               query: str,
               vectors: List[List[float]],
               limit: int = 5,
               filters: Optional[Dict] = None) -> list[OutputData]:
        # Check if hybrid search is enabled, and we have query text
        # Full-text search is always enabled by default
        if self.hybrid_search and query:
            return self._hybrid_search(query, vectors, limit, filters)
        else:
            return self._vector_search(query, vectors, limit, filters)

    def _vector_search(self,
                       query: str,
                       vectors: List[List[float]],
                       limit: int = 5,
                       filters: Optional[Dict] = None) -> list[OutputData]:
        """Perform pure vector search."""
        # mem0 passes a single vector as 'vectors' parameter, but we expect List[List[float]]
        # Handle both cases: single vector or list of vectors
        # If vectors is a single vector (list of floats), use it directly
        if isinstance(vectors, list) and len(vectors) > 0 and isinstance(vectors[0], (int, float)):
            query_vector = vectors
        # If vectors is a list of vectors, use the first one
        elif isinstance(vectors, list) and len(vectors) > 0 and isinstance(vectors[0], list):
            query_vector = vectors[0]
        else:
            return []

        # Build where clause from filters
        where_clause = self._generate_where_clause(filters)

        # Perform vector search - pyobvector expects a single vector, not a list of vectors
        results = self.obvector.ann_search(
            table_name=self.collection_name,
            vec_data=query_vector if not self.normalize else self._normalize(query_vector),
            vec_column_name=self.vector_field,
            distance_func=self._get_distance_function(self.vidx_metric_type),
            with_dist=True,
            topk=limit,
            output_column_names=[
                self.text_field,
                self.metadata_field,
                self.primary_field,
                "user_id",
                "agent_id",
                "run_id",
                "actor_id",
                "hash",
                "created_at",
                "updated_at",
                "category",
            ],
            where_clause=where_clause,
        )

        # Convert results to OutputData objects
        search_results = []
        for row in results.fetchall():
            (text_content, metadata_json, vector_id, user_id, agent_id, run_id,
             actor_id, hash_val, created_at, updated_at, category, distance) = self._parse_row(row)

            # Build standard metadata
            metadata = self._build_standard_metadata(
                user_id, agent_id, run_id, actor_id, hash_val,
                created_at, updated_at, category, metadata_json
            )

            # Convert distance to score based on metric type
            if self.vidx_metric_type == "l2":
                # For L2 distance, lower is better, so we can use 1/(1+distance) or just use distance
                score = float(distance)
            elif self.vidx_metric_type == "cosine":
                # For cosine distance, lower is better
                score = float(distance)
            elif self.vidx_metric_type == "inner_product":
                # For inner product, higher is better, so we negate the distance
                score = -float(distance)
            else:
                score = float(distance)

            search_results.append(self._create_output_data(vector_id, text_content, score, metadata))
        logger.info(f"_vector_search results, len : {len(search_results)}, search_results : {search_results}")
        return search_results

    def _fulltext_search(self, query: str, limit: int = 5, filters: Optional[Dict] = None) -> list[OutputData]:
        """Perform full-text search using OceanBase FTS with parameterized queries including score."""
        # Generate where clause from filters using the existing method
        filter_where_clause = self._generate_where_clause(filters)

        # Build the full-text search condition using SQLAlchemy text with parameters
        # Use the same parameter format that SQLAlchemy will use for other parameters
        fts_condition = text(f"MATCH({self.fulltext_field}) AGAINST(:query IN NATURAL LANGUAGE MODE)").bindparams(
            bindparam("query", query)
        )

        # Combine FTS condition with filter conditions
        where_conditions = [fts_condition]
        if filter_where_clause:
            where_conditions.extend(filter_where_clause)

        # Build custom query to include score field
        try:
            # Build select statement with specific columns AND score
            columns = [
                self.table.c[self.text_field],
                self.table.c[self.metadata_field],
                self.table.c[self.primary_field],
                self.table.c["user_id"],
                self.table.c["agent_id"],
                self.table.c["run_id"],
                self.table.c["actor_id"],
                self.table.c["hash"],
                self.table.c["created_at"],
                self.table.c["updated_at"],
                self.table.c["category"],
                # Add the score calculation as a column
                text(f"MATCH({self.fulltext_field}) AGAINST(:query IN NATURAL LANGUAGE MODE) as score").bindparams(
                    bindparam("query", query)
                )
            ]

            stmt = select(*columns)

            # Add where conditions
            for condition in where_conditions:
                stmt = stmt.where(condition)

            # Order by score DESC to get best matches first
            stmt = stmt.order_by(text('score DESC'))

            # Add limit
            if limit:
                stmt = stmt.limit(limit)

            # Execute the query with parameters - use direct parameter passing
            with self.obvector.engine.connect() as conn:
                with conn.begin():
                    logger.info(f"Executing FTS query with parameters: query={query}")
                    # Execute with parameter dictionary - the standard SQLAlchemy way
                    results = conn.execute(stmt)
                    rows = results.fetchall()

        except Exception as e:
            logger.warning(f"Full-text search failed, falling back to LIKE search: {e}")
            try:
                # Fallback to simple LIKE search with parameters
                like_query = f"%{query}%"
                like_condition = text(f"{self.fulltext_field} LIKE :like_query").bindparams(
                    bindparam("like_query", like_query)
                )

                fallback_conditions = [like_condition]
                if filter_where_clause:
                    fallback_conditions.extend(filter_where_clause)

                # Build fallback query with default score
                columns = [
                    self.table.c[self.text_field],
                    self.table.c[self.metadata_field],
                    self.table.c[self.primary_field],
                    self.table.c["user_id"],
                    self.table.c["agent_id"],
                    self.table.c["run_id"],
                    self.table.c["actor_id"],
                    self.table.c["hash"],
                    self.table.c["created_at"],
                    self.table.c["updated_at"],
                    self.table.c["category"],
                    # Default score for LIKE search
                    '1.0 as score'
                ]

                stmt = select(*columns)

                for condition in fallback_conditions:
                    stmt = stmt.where(condition)

                if limit:
                    stmt = stmt.limit(limit)

                # Execute fallback query with parameters
                with self.obvector.engine.connect() as conn:
                    with conn.begin():
                        logger.info(f"Executing LIKE fallback query with parameters: like_query={like_query}")
                        # Execute with parameter dictionary - the standard SQLAlchemy way
                        results = conn.execute(stmt)
                        rows = results.fetchall()
            except Exception as fallback_error:
                logger.error(f"Both full-text search and LIKE fallback failed: {fallback_error}")
                return []

        # Convert results to OutputData objects
        fts_results = []
        for row in rows:
            # Parse the row data including score as the last column
            (text_content, metadata_json, vector_id, user_id, agent_id, run_id, actor_id, hash_val,
             created_at, updated_at, category, fts_score) = self._parse_row(row)

            # Build standard metadata
            metadata = self._build_standard_metadata(
                user_id, agent_id, run_id, actor_id, hash_val,
                created_at, updated_at, category, metadata_json
            )

            # Use the actual FTS score from the query
            fts_results.append(self._create_output_data(vector_id, text_content, float(fts_score), metadata))

        logger.info(f"_fulltext_search results, len : {len(fts_results)}, fts_results : {fts_results}")
        return fts_results

    def _hybrid_search(self, query: str, vectors: List[List[float]], limit: int = 5, filters: Optional[Dict] = None,
                       fusion_method: str = "rrf", k: int = 60):
        """Perform hybrid search combining vector and full-text search."""

        # Perform vector search
        vector_results = self._vector_search(query, vectors, limit, filters)

        # Perform full-text search
        fts_results = self._fulltext_search(query, limit, filters)

        # Combine and rerank results using specified fusion method
        hybrid_results = self._combine_search_results(
            vector_results, fts_results, limit, fusion_method, k
        )
        logger.info(f"_hybrid_search results, len : {len(hybrid_results)}, hybrid_results : {hybrid_results}")
        return hybrid_results

    def _combine_search_results(self, vector_results: List[OutputData], fts_results: List[OutputData],
                                limit: int, fusion_method: str = "rrf", k: int = 60):
        """Combine and rerank vector and full-text search results using RRF or weighted fusion."""
        if fusion_method == "rrf":
            return self._rrf_fusion(vector_results, fts_results, limit, k)
        else:
            return self._weighted_fusion(vector_results, fts_results, limit)

    def _rrf_fusion(self, vector_results: List[OutputData], fts_results: List[OutputData],
                    limit: int, k: int = 60):
        """Reciprocal Rank Fusion (RRF) for combining search results."""
        # Create mapping of document ID to result data
        all_docs = {}

        # Process vector search results (rank-based scoring)
        for rank, result in enumerate(vector_results, 1):
            rrf_score = 1.0 / (k + rank)
            all_docs[result.id] = {
                'result': result,
                'vector_rank': rank,
                'fts_rank': None,
                'rrf_score': rrf_score
            }

        # Process FTS results (add or update RRF scores)
        for rank, result in enumerate(fts_results, 1):
            fts_rrf_score = 1.0 / (k + rank)

            if result.id in all_docs:
                # Document found in both searches - combine RRF scores
                all_docs[result.id]['fts_rank'] = rank
                all_docs[result.id]['rrf_score'] += fts_rrf_score
            else:
                # Document only in FTS results
                all_docs[result.id] = {
                    'result': result,
                    'vector_rank': None,
                    'fts_rank': rank,
                    'rrf_score': fts_rrf_score
                }

        # Convert to final results and sort by RRF score
        heap = []
        for doc_id, doc_data in all_docs.items():
            # Use document ID as tiebreaker to avoid dict comparison when rrf_scores are equal
            if len(heap) < limit:
                heapq.heappush(heap, (doc_data['rrf_score'], doc_id, doc_data))
            elif doc_data['rrf_score'] > heap[0][0]:
                heapq.heapreplace(heap, (doc_data['rrf_score'], doc_id, doc_data))

        final_results = []
        for score, _, doc_data in sorted(heap, key=lambda x: x[0], reverse=True):
            result = doc_data['result']
            result.score = score
            # Add ranking information to metadata for debugging
            result.payload['_fusion_info'] = {
                'vector_rank': doc_data['vector_rank'],
                'fts_rank': doc_data['fts_rank'],
                'rrf_score': score,
                'fusion_method': 'rrf'
            }
            final_results.append(result)

        return final_results

    def _weighted_fusion(self, vector_results: List[OutputData], fts_results: List[OutputData],
                         limit: int, vector_weight: float = 0.7, text_weight: float = 0.3):
        """Traditional weighted score fusion (fallback method)."""
        # Create a mapping of id to results for deduplication
        combined_results = {}

        # Normalize vector scores to 0-1 range
        if vector_results:
            vector_scores = [result.score for result in vector_results]
            min_vector_score = min(vector_scores)
            max_vector_score = max(vector_scores)
            vector_score_range = max_vector_score - min_vector_score

            for result in vector_results:
                if vector_score_range > 0:
                    # For distance metrics, lower is better, so we invert the normalized score
                    if self.vidx_metric_type in ["l2", "cosine"]:
                        normalized_score = 1.0 - (result.score - min_vector_score) / vector_score_range
                    else:  # inner_product
                        normalized_score = (result.score - min_vector_score) / vector_score_range
                else:
                    normalized_score = 1.0

                combined_results[result.id] = {
                    'result': result,
                    'vector_score': normalized_score,
                    'fts_score': 0.0
                }

        # Add FTS results (FTS scores are already normalized to 0-1)
        for result in fts_results:
            if result.id in combined_results:
                # Update existing result with FTS score
                combined_results[result.id]['fts_score'] = result.score
            else:
                # Add new FTS-only result
                combined_results[result.id] = {
                    'result': result,
                    'vector_score': 0.0,
                    'fts_score': result.score
                }

        # Calculate combined scores and create final results
        heap = []
        for doc_id, doc_data in combined_results.items():
            combined_score = (vector_weight * doc_data['vector_score'] +
                              text_weight * doc_data['fts_score'])

            if len(heap) < limit:
                heapq.heappush(heap, (combined_score, doc_id, doc_data))
            elif combined_score > heap[0][0]:
                heapq.heapreplace(heap, (combined_score, doc_id, doc_data))

        final_results = []
        for score, _, doc_data in sorted(heap, key=lambda x: x[0], reverse=True):
            result = doc_data['result']
            result.score = score
            # Add fusion info for debugging
            result.payload['_fusion_info'] = {
                'vector_score': doc_data['vector_score'],
                'fts_score': doc_data['fts_score'],
                'combined_score': score,
                'fusion_method': 'weighted'
            }
            final_results.append(result)

        # Return top results
        return final_results

    def delete(self, vector_id: str):
        """Delete a vector by ID."""
        self.obvector.delete(
            table_name=self.collection_name,
            ids=[vector_id],
        )

    def update(self, vector_id: str, vector: Optional[List[float]] = None, payload: Optional[Dict] = None):
        """Update a vector and its payload."""
        # Get existing record
        existing = self.obvector.get(
            table_name=self.collection_name,
            ids=[vector_id],
        )

        if not existing.fetchall():
            return

        # Prepare update data
        update_data: Dict[str, Any] = {
            self.primary_field: vector_id,
        }

        if vector is not None:
            update_data[self.vector_field] = (
                vector if not self.normalize else self._normalize(vector)
            )

        if payload is not None:
            # Use the helper method to build fields, then merge with update_data
            temp_record = self._build_record_for_insert(vector_id, vector or [], payload)

            # Copy relevant fields from temp_record (excluding primary key and vector if not updating)
            for key, value in temp_record.items():
                if key != self.primary_field and (vector is not None or key != self.vector_field):
                    update_data[key] = value

        # Update record
        self.obvector.upsert(
            table_name=self.collection_name,
            data=[update_data],
        )

    def get(self, vector_id: str):
        """Retrieve a vector by ID."""
        results = self.obvector.get(
            table_name=self.collection_name,
            ids=[vector_id],
            output_column_name=[
                self.vector_field,
                self.text_field,
                self.metadata_field,
                "user_id",
                "agent_id",
                "run_id",
                "actor_id",
                "hash",
                "created_at",
                "updated_at",
                "category",
            ],
        )

        rows = results.fetchall()
        if not rows:
            return None

        (vector, text_content, metadata_json, user_id, agent_id,
         run_id, actor_id, hash_val, created_at, updated_at, category, _) = self._parse_row(rows[0])

        # Build standard metadata
        metadata = self._build_standard_metadata(
            user_id, agent_id, run_id, actor_id, hash_val,
            created_at, updated_at, category, metadata_json
        )

        return self._create_output_data(vector_id, text_content, 0.0, metadata)

    def list_cols(self):
        """List all collections."""
        # Get all tables from the database using the correct SQLAlchemy API
        with self.obvector.engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES"))
            tables = [row[0] for row in result.fetchall()]
            return tables

    def delete_col(self):
        """Delete the collection."""
        if self.obvector.check_table_exists(self.collection_name):
            self.obvector.drop_table_if_exist(self.collection_name)

    def _get_existing_vector_dimension(self) -> Optional[int]:
        """Get the dimension of the existing vector field in the table."""
        if not self.obvector.check_table_exists(self.collection_name):
            return None

        try:
            # Get table schema information using the correct SQLAlchemy API
            with self.obvector.engine.connect() as conn:
                result = conn.execute(text(f"DESCRIBE {self.collection_name}"))
                columns = result.fetchall()

            # Find the vector field and extract its dimension
            for col in columns:
                if col[0] == self.vector_field:
                    # Parse vector type like "VECTOR(1536)" to extract dimension
                    col_type = col[1]
                    if col_type.startswith("VECTOR(") and col_type.endswith(")"):
                        dim_str = col_type[7:-1]  # Extract dimension from "VECTOR(1536)"
                        return int(dim_str)
            return None
        except Exception as e:
            logger.warning(f"Failed to get vector dimension for table {self.collection_name}: {e}")
            return None

    def col_info(self):
        """Get information about the collection."""
        if not self.obvector.check_table_exists(self.collection_name):
            return None

        # Get table schema information using the correct SQLAlchemy API
        with self.obvector.engine.connect() as conn:
            result = conn.execute(text(f"DESCRIBE {self.collection_name}"))
            columns = result.fetchall()

        return {
            "name": self.collection_name,
            "columns": [{"name": col[0], "type": col[1]} for col in columns],
            "index_type": self.index_type,
            "metric_type": self.vidx_metric_type,
        }

    def list(self, filters: Optional[Dict] = None, limit: Optional[int] = None):
        """List all memories."""
        # Build where clause from filters
        where_clause = self._generate_where_clause(filters)

        # Get all records
        results = self.obvector.get(
            table_name=self.collection_name,
            ids=None,
            output_column_name=[
                self.primary_field,
                self.vector_field,
                self.text_field,
                self.metadata_field,
                "user_id",
                "agent_id",
                "run_id",
                "actor_id",
                "hash",
                "created_at",
                "updated_at",
                "category",
            ],
            where_clause=where_clause
        )

        memories = []
        for row in results.fetchall():
            (vector_id, vector, text_content, metadata_json, user_id, agent_id, run_id,
             actor_id, hash_val, created_at, updated_at, category) = self._parse_row(row)

            # Build standard metadata
            metadata = self._build_standard_metadata(
                user_id, agent_id, run_id, actor_id, hash_val,
                created_at, updated_at, category, metadata_json
            )

            memories.append(self._create_output_data(vector_id, text_content, 0.0, metadata))

        if limit:
            memories = memories[:limit]

        return [memories]

    def reset(self):
        """Reset by deleting the collection and recreating it."""
        self.delete_col()
        if self.embedding_model_dims is not None:
            self._create_table_with_index_by_embedding_model_dims()

        if self.hybrid_search:
            self._check_and_create_fulltext_index()

    def _check_and_create_fulltext_index(self):
        # Check whether the full-text index exists, if not, create it
        if not self._check_fulltext_index_exists():
            self._create_fulltext_index()

    def _check_fulltext_index_exists(self) -> bool:
        """
        Check if the full-text index of the specified table exists.
        """
        try:
            with self.obvector.engine.connect() as conn:
                result = conn.execute(text(f"SHOW INDEX FROM {self.collection_name}"))
                indexes = result.fetchall()

                for index in indexes:
                    # Index [2] is the index name, index [4] is the column name, and index [10] is the index type
                    if len(index) > 10 and index[10] == 'FULLTEXT':
                        if self.fulltext_field in str(index[4]):
                            return True

            return False

        except Exception as e:
            logger.error(f"An error occurred while checking the full-text index: {e}")
            return False

    def _create_fulltext_index(self):
        try:
            logger.debug(
                "About to create fulltext index for collection '%s' using parser '%s'",
                self.collection_name,
                self.fulltext_parser,
            )

            # Create fulltext index with the specified parser using SQL
            with self.obvector.engine.connect() as conn:
                sql_command = text(f"""ALTER TABLE {self.collection_name}
                    ADD FULLTEXT INDEX fulltext_index_for_col_text ({self.fulltext_field}) WITH PARSER {self.fulltext_parser}""")

                logger.debug("DEBUG: Executing SQL: %s", sql_command)
                conn.execute(sql_command)
                logger.debug("DEBUG: Fulltext index created successfully for '%s'", self.collection_name)

        except Exception as e:
            logger.exception("Exception occurred while creating fulltext index")
            raise Exception(
                "Failed to add fulltext index to the target table, your OceanBase version must be "
                "4.3.5.1 or above to support fulltext index and vector index in the same table"
            ) from e

        # Refresh metadata
        self.obvector.refresh_metadata([self.collection_name])