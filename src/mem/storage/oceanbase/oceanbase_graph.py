"""
OceanBase graph storage implementation

This module provides OceanBase-based graph storage for memory data.
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from pyobvector import ObVecClient, l2_distance, VECTOR, VecIndexType
from sqlalchemy import bindparam, text, MetaData, Column, String, Integer, Index, Table
from sqlalchemy.dialects.mysql import TIMESTAMP

from mem.integrations import EmbeddingFactory, LLMFactory
from mem.utils.utils import format_entities, remove_code_blocks

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    raise ImportError("rank_bm25 is not installed. Please install it using pip install rank-bm25")

from mem.prompts import GraphPrompts, GraphToolsPrompts

from mem.storage.oceanbase import constants

logger = logging.getLogger(__name__)


class MemoryGraph:
    """OceanBase-based graph memory storage implementation."""

    def __init__(self, config: Any) -> None:
        """Initialize OceanBase graph memory.

        Args:
            config: Memory configuration containing graph_store, embedder, and llm configs.

        Raises:
            ValueError: If embedding_model_dims is not configured.
        """
        self.config = config

        # Get OceanBase config
        ob_config = self.config.graph_store.config

        # Get embedding_model_dims (required)
        if (not hasattr(ob_config, "embedding_model_dims") or
                ob_config.embedding_model_dims is None):
            raise ValueError(
                "embedding_model_dims is required for OceanBase graph operations. "
                "Please configure embedding_model_dims in your OceanBaseGraphConfig."
            )
        self.embedding_dims = ob_config.embedding_model_dims

        # Get vidx parameters with defaults.
        self.index_type = getattr(ob_config, "index_type", constants.DEFAULT_INDEX_TYPE)
        self.vidx_metric_type = getattr(ob_config, "vidx_metric_type", constants.DEFAULT_OCEANBASE_VECTOR_METRIC_TYPE)
        self.vidx_name = getattr(ob_config, "vidx_name", constants.DEFAULT_VIDX_NAME)

        # Get graph search parameters
        self.max_hops = getattr(ob_config, "max_hops", 3)

        # Set vidx_algo_params with defaults based on index_type.
        self.vidx_algo_params = getattr(ob_config, "vidx_algo_params", None)
        if not self.vidx_algo_params:
            # Set default parameters based on index type.
            self.vidx_algo_params = constants.get_default_build_params(self.index_type)

        # Initialize embedding model
        self.embedding_model = EmbeddingFactory.create(
            self.config.embedder.provider,
            self.config.embedder.config,
            self.config.vector_store.config,
        )

        # Initialize OceanBase client
        self.client = ObVecClient(
            uri=f"{ob_config.host}:{ob_config.port}",
            user=ob_config.user,
            password=ob_config.password,
            db_name=ob_config.db_name,
        )
        self.engine = self.client.engine
        self.metadata = MetaData()

        # Create tables
        self._create_tables()

        # Initialize LLM.
        self.llm_provider = self._get_llm_provider()
        llm_config = self._get_llm_config()
        self.llm = LLMFactory.create(self.llm_provider, llm_config)

        # Initialize graph prompts and tools
        self.graph_prompts = GraphPrompts()
        self.graph_tools_prompts = GraphToolsPrompts()

    def _get_llm_provider(self) -> str:
        """Get LLM provider from configuration with fallback.

        Returns:
            LLM provider name.
        """
        # Check graph_store.llm.provider first
        if (self.config.graph_store and
                self.config.graph_store.llm and
                self.config.graph_store.llm.provider):
            return self.config.graph_store.llm.provider

        # Check config.llm.provider
        if self.config.llm and self.config.llm.provider:
            return self.config.llm.provider

        # Default fallback
        return constants.DEFAULT_LLM_PROVIDER

    def _get_llm_config(self) -> Optional[Any]:
        """Get LLM config from configuration.

        Returns:
            LLM configuration object or None.
        """
        # Check graph_store.llm.config first
        if (self.config.graph_store and
                self.config.graph_store.llm and
                hasattr(self.config.graph_store.llm, "config")):
            return self.config.graph_store.llm.config

        # Check config.llm.config
        if hasattr(self.config.llm, "config"):
            return self.config.llm.config

        return None

    def _build_user_identity(self, filters: Dict[str, Any]) -> str:
        """Build user identity string from filters.

        Args:
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Formatted user identity string.
        """
        identity_parts = [f"user_id: {filters['user_id']}"]

        if filters.get("agent_id"):
            identity_parts.append(f"agent_id: {filters['agent_id']}")

        if filters.get("run_id"):
            identity_parts.append(f"run_id: {filters['run_id']}")

        return ", ".join(identity_parts)

    def _build_filter_conditions(
            self,
            filters: Dict[str, Any],
            prefix: str = ""
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Build SQL filter conditions and parameters from filters.

        Args:
            filters: Dictionary containing user_id, agent_id, run_id.
            prefix: Optional prefix for column names (e.g., "r." for table alias).

        Returns:
            Tuple of (filter_conditions_list, params_dict).
        """
        filter_parts = [f"{prefix}user_id = :user_id"]
        params = {"user_id": filters["user_id"]}

        if filters.get("agent_id"):
            filter_parts.append(f"{prefix}agent_id = :agent_id")
            params["agent_id"] = filters["agent_id"]

        if filters.get("run_id"):
            filter_parts.append(f"{prefix}run_id = :run_id")
            params["run_id"] = filters["run_id"]

        return filter_parts, params

    @staticmethod
    def _coerce_tool_response_to_dict(response: Any) -> Dict[str, Any]:
        """Ensure LLM tool response is a dict.

        Some LLM providers may return a JSON string instead of a parsed dict. This helper
        normalizes the response to a dictionary with safe fallbacks.

        Args:
            response: LLM tool call response, may be dict or JSON string.

        Returns:
            Normalized dictionary object, or empty dict if unparseable.
        """
        if isinstance(response, dict):
            return response
        if isinstance(response, str):
            try:
                cleaned = remove_code_blocks(response)
            except Exception:
                cleaned = response
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        # Fallback to empty dict if un-parseable
        return {}

    def _create_tables(self) -> None:
        """Create graph entities and relationships tables if they don't exist.

        Creates two tables:
            - graph_entities: Stores entity nodes and their vector embeddings
            - graph_relationships: Stores relationships between entities
        """

        if not self.client.check_table_exists(constants.TABLE_ENTITIES):
            # Define columns for entities table
            cols = [
                Column("id", String(64), primary_key=True, autoincrement=False),
                Column("name", String(255), nullable=False),
                Column("entity_type", String(64)),
                Column("embedding", VECTOR(self.embedding_dims)),
                Column("mentions", Integer, default=1),
                Column("created_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")),
                Column("updated_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            ]
            # Define regular indexes
            indexes = [
                Index("idx_name", "name"),
            ]

            # Map index_type string to VecIndexType enum
            index_type_map = constants.OCEANBASE_SUPPORTED_VECTOR_INDEX_TYPES

            # Create vector index parameters
            vidx_params = self.client.prepare_index_params()
            vidx_params.add_index(
                field_name="embedding",
                index_type=index_type_map.get(self.index_type, VecIndexType.HNSW),
                index_name=self.vidx_name,
                metric_type=self.vidx_metric_type,
                params=self.vidx_algo_params,
            )

            # Create table with vector index
            self.client.create_table_with_index_params(
                table_name=constants.TABLE_ENTITIES,
                columns=cols,
                indexes=indexes,
                vidxs=vidx_params,
                partitions=None,
            )

            logger.info("%s table created successfully", constants.TABLE_ENTITIES)
        else:
            logger.info("%s table already exists", constants.TABLE_ENTITIES)
            # Check vector dimension consistency
            existing_dim = self._get_existing_vector_dimension_for_entities()
            if existing_dim is not None and existing_dim != self.embedding_dims:
                raise ValueError(
                    f"Vector dimension mismatch: existing table '{constants.TABLE_ENTITIES}' has "
                    f"vector dimension {existing_dim}, but requested dimension is {self.embedding_dims}. "
                    f"Please use a different configuration or reset the graph."
                )

        # Create relationships table using pyobvector API
        if not self.client.check_table_exists(constants.TABLE_RELATIONSHIPS):

            # Define columns for relationships table
            cols = [
                Column("id", String(64), primary_key=True, autoincrement=False),
                Column("source_entity_id", String(64), nullable=False),
                Column("relationship_type", String(128), nullable=False),
                Column("destination_entity_id", String(64), nullable=False),
                Column("user_id", String(128)),
                Column("agent_id", String(128)),
                Column("run_id", String(128)),
                Column("mentions", Integer, default=1),
                Column("created_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP")),
                Column("updated_at", TIMESTAMP, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
            ]

            # Define regular indexes
            indexes = [
                Index("idx_source_dest", "source_entity_id", "destination_entity_id"),
            ]

            # Create table without vector index (relationships table has no vectors)
            self.client.create_table_with_index_params(
                table_name=constants.TABLE_RELATIONSHIPS,
                columns=cols,
                indexes=indexes,
                vidxs=None,
                partitions=None,
            )

            logger.info("%s table created successfully", constants.TABLE_RELATIONSHIPS)
        else:
            logger.info("%s table already exists", constants.TABLE_RELATIONSHIPS)

    def _get_existing_vector_dimension_for_entities(self) -> Optional[int]:
        """Get the dimension of the existing vector field in entities table.

        Returns:
            Dimension of the vector field, or None if table doesn't exist or field not found.
        """
        if not self.client.check_table_exists(constants.TABLE_ENTITIES):
            return None

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(f"DESCRIBE {constants.TABLE_ENTITIES}"))
                columns = result.fetchall()

            for col in columns:
                if col[0] == "embedding":
                    col_type = col[1]
                    if col_type.startswith("VECTOR(") and col_type.endswith(")"):
                        dim_str = col_type[7:-1]
                        return int(dim_str)
            return None
        except Exception as e:
            logger.warning(
                "Failed to get vector dimension for %s: %s",
                constants.TABLE_ENTITIES,
                e
            )
            return None

    def _build_where_clause_with_filters(
            self,
            filters: Dict[str, Any],
            prefix: str = "",
            additional_params: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """Build where clause and parameters from filters using bindparam.

        Args:
            filters: Dictionary containing user_id, agent_id, run_id.
            prefix: Optional prefix for column names (e.g., "r." for table alias).
            additional_params: Optional additional parameters to include.

        Returns:
            Tuple of (where_clause_list, params_dict).
        """
        where_conditions = []
        params = {"user_id": filters["user_id"]}
        where_conditions.append(f"{prefix}user_id = :user_id")

        if filters.get("agent_id"):
            where_conditions.append(f"{prefix}agent_id = :agent_id")
            params["agent_id"] = filters["agent_id"]
        if filters.get("run_id"):
            where_conditions.append(f"{prefix}run_id = :run_id")
            params["run_id"] = filters["run_id"]

        # Add additional params if provided
        if additional_params:
            params.update(additional_params)

        where_sql = " AND ".join(where_conditions)
        where_clause_with_params = text(where_sql).bindparams(**params)
        return [where_clause_with_params], params

    def add(self, data: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Add data to the graph.

        Args:
            data: The data to add to the graph.
            filters: Dictionary containing filters (user_id, agent_id, run_id).

        Returns:
            Dictionary containing deleted_entities and added_entities.
        """
        entity_type_map = self._retrieve_nodes_from_data(data, filters)
        to_be_added = self._establish_nodes_relations_from_data(data, filters, entity_type_map)
        search_output = self._search_graph_db(node_list=list(entity_type_map.keys()), filters=filters)
        to_be_deleted = self._get_delete_entities_from_search_output(search_output, data, filters)

        deleted_entities = self._delete_entities(to_be_deleted, filters)
        added_entities = self._add_entities(to_be_added, filters, entity_type_map)
        logger.debug("Deleted entities: %s, Added entities: %s", deleted_entities, added_entities)
        return {"deleted_entities": deleted_entities, "added_entities": added_entities}

    def search(self, query: str, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, str]]:
        """Search for memories and related graph data.

        Args:
            query: Query to search for.
            filters: Dictionary containing filters (user_id, agent_id, run_id).
            limit: Maximum number of nodes and relationships to retrieve. Defaults to 100.

        Returns:
            List of search results containing source, relationship, and destination.
        """
        entity_type_map = self._retrieve_nodes_from_data(query, filters)
        search_output = self._search_graph_db(node_list=list(entity_type_map.keys()), filters=filters, limit=limit)

        if not search_output:
            return []

        search_outputs_sequence = [
            [item["source"], item["relationship"], item["destination"]] for item in search_output
        ]
        bm25 = BM25Okapi(search_outputs_sequence)

        tokenized_query = query.split(" ")
        reranked_results = bm25.get_top_n(tokenized_query, search_outputs_sequence, n=constants.DEFAULT_BM25_TOP_N)

        search_results = []
        for item in reranked_results:
            search_results.append({"source": item[0], "relationship": item[1], "destination": item[2]})

        logger.info("Returned %d search results", len(search_results))

        return search_results

    def delete_all(self, filters: Dict[str, Any]) -> None:
        """Delete all graph data for the given filters.

        Args:
            filters: Filters containing user_id, agent_id, run_id.
        """
        where_clause, _ = self._build_where_clause_with_filters(filters)

        try:
            relationships_results = self.client.get(
                table_name=constants.TABLE_RELATIONSHIPS,
                ids=None,
                output_column_name=["id", "source_entity_id", "destination_entity_id"],
                where_clause=where_clause
            )

            # Collect unique entity IDs from relationships
            entity_ids = set()
            for rel in relationships_results.fetchall():
                entity_ids.add(rel[1])  # source_entity_id
                entity_ids.add(rel[2])  # destination_entity_id

            # Delete the relationships
            self.client.delete(
                table_name=constants.TABLE_RELATIONSHIPS,
                where_clause=where_clause
            )
            logger.info("Deleted relationships for filters: %s", filters)

            # Delete entities that were part of these relationships
            if entity_ids:
                self.client.delete(
                    table_name=constants.TABLE_ENTITIES,
                    ids=list(entity_ids),
                )
                logger.info("Deleted %d entities for filters: %s", len(entity_ids), filters)
        except Exception as e:
            logger.warning("Error deleting graph data: %s", e)

        logger.info("Deleted all graph data for filters: %s", filters)

    def get_all(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, str]]:
        """Retrieve all nodes and relationships from the graph database.

        Args:
            filters: Dictionary containing filters (user_id, agent_id, run_id).
            limit: Maximum number of relationships to retrieve. Defaults to 100.

        Returns:
            List of dictionaries containing source, relationship, and target.
        """

        where_clause, params = self._build_where_clause_with_filters(filters)

        relationships_results = self.client.get(
            table_name=constants.TABLE_RELATIONSHIPS,
            ids=None,
            output_column_name=["id", "source_entity_id", "relationship_type", "destination_entity_id", "updated_at"],
            where_clause=where_clause
        )

        relationships = relationships_results.fetchall()
        if not relationships:
            return []

        # Limit results if needed
        if len(relationships) > limit:
            relationships = relationships[:limit]

        # Extract unique entity IDs from relationships
        entity_ids = set()
        for rel in relationships:
            entity_ids.add(rel[1])  # source_entity_id
            entity_ids.add(rel[3])  # destination_entity_id

        # Get all entities that are referenced in relationships
        entities_results = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=list(entity_ids),
            output_column_name=["id", "name"]
        )

        # Create a mapping from entity_id to entity_name
        entity_map = {entity[0]: entity[1] for entity in entities_results.fetchall()}

        # Build final results with updated_at for sorting
        final_results = []
        for rel in relationships:
            rel_id, source_id, relationship_type, dest_id, updated_at = rel

            source_name = entity_map.get(source_id, f"Unknown_{source_id}")
            dest_name = entity_map.get(dest_id, f"Unknown_{dest_id}")

            final_results.append({
                "source": source_name,
                "relationship": relationship_type,
                "target": dest_name,
                "_updated_at": updated_at,  # Keep for sorting
            })

        # Sort by updated_at (descending)
        final_results.sort(key=lambda x: x["_updated_at"], reverse=True)

        # Remove the temporary _updated_at field
        for result in final_results:
            del result["_updated_at"]

        logger.info("Retrieved %d relationships", len(final_results))
        return final_results

    def _retrieve_nodes_from_data(self, data: str, filters: Dict[str, Any]) -> Dict[str, str]:
        """Extract all the entities mentioned in the query.

        Args:
            data: Input text to extract entities from.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Dictionary mapping entity names to entity types.
        """
        _tools = [self.graph_tools_prompts.get_extract_entities_tool()]
        if constants.is_structured_llm_provider(self.llm_provider):
            _tools = [self.graph_tools_prompts.get_extract_entities_tool(structured=True)]

        search_results = self.llm.generate_response(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a smart assistant who understands entities and their types in a given text. "
                        f"If user message contains self reference such as 'I', 'me', 'my' etc. "
                        f"then use {filters['user_id']} as the source entity. "
                        f"Extract all the entities from the text. "
                        f"***DO NOT*** answer the question itself if the given text is a question."
                    ),
                },
                {"role": "user", "content": data},
            ],
            tools=_tools,
        )

        # Normalize potential string response to dict
        search_results = self._coerce_tool_response_to_dict(search_results)

        entity_type_map = {}

        try:
            for tool_call in search_results.get("tool_calls", []):
                if tool_call["name"] != "extract_entities":
                    continue
                for item in tool_call["arguments"]["entities"]:
                    entity_type_map[item["entity"]] = item["entity_type"]
        except Exception as e:
            logger.exception(
                "Error in search tool: %s, llm_provider=%s, search_results=%s",
                e,
                self.llm_provider,
                search_results
            )

        entity_type_map = {
            k.lower().replace(" ", "_"): v.lower().replace(" ", "_")
            for k, v in entity_type_map.items()
        }
        logger.debug("Entity type map: %s\n search_results=%s", entity_type_map, search_results)
        return entity_type_map

    def _establish_nodes_relations_from_data(
            self,
            data: str,
            filters: Dict[str, Any],
            entity_type_map: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Establish relations among the extracted nodes.

        Args:
            data: Input text to extract relationships from.
            filters: Dictionary containing user_id, agent_id, run_id.
            entity_type_map: Mapping of entity names to types.

        Returns:
            List of dictionaries containing source, destination, and relationship.
        """
        user_identity = self._build_user_identity(filters)

        if self.config.graph_store.custom_prompt:
            system_content = self.graph_prompts.get_system_prompt("extract_relations")
            system_content = system_content.replace("USER_ID", user_identity)
            system_content = system_content.replace("CUSTOM_PROMPT", f"4. {self.config.graph_store.custom_prompt}")
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": data},
            ]
        else:
            system_content = self.graph_prompts.get_system_prompt("extract_relations")
            system_content = system_content.replace("USER_ID", user_identity)
            messages = [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": f"List of entities: {list(entity_type_map.keys())}. \n\nText: {data}"
                },
            ]

        _tools = [self.graph_tools_prompts.get_relations_tool()]
        if constants.is_structured_llm_provider(self.llm_provider):
            _tools = [self.graph_tools_prompts.get_relations_tool(structured=True)]

        extracted_entities = self.llm.generate_response(
            messages=messages,
            tools=_tools,
        )

        # Normalize to dict for consistent access
        extracted_entities = self._coerce_tool_response_to_dict(extracted_entities)

        entities = []
        if extracted_entities.get("tool_calls"):
            first_call = (
                extracted_entities["tool_calls"][0]
                if extracted_entities["tool_calls"]
                else {}
            )
            entities = first_call.get("arguments", {}).get("entities", [])

        entities = self._remove_spaces_from_entities(entities)
        logger.debug("Extracted entities: %s", entities)
        return entities

    def _search_graph_db(
            self,
            node_list: List[str],
            filters: Dict[str, Any],
            limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search similar nodes and their relationships using vector similarity with multi-hop support.

        Supports up to 3-hop graph traversal using recursive CTE. Results are prioritized by
        path length (1-hop first, then 2-hop, then 3-hop).

        Args:
            node_list: List of node names to search for.
            filters: Dictionary containing user_id, agent_id, run_id.
            limit: Maximum number of results to return. Defaults to 100.

        Returns:
            List of dictionaries containing source, relationship, destination and their IDs.
        """
        result_relations = []

        for node in node_list:
            n_embedding = self.embedding_model.embed(node)

            entities = self._search_node(None, n_embedding, filters, limit=limit)

            if not entities:
                continue

            entity_ids = [e.get("id") for e in entities]

            # Use recursive CTE for multi-hop traversal
            multi_hop_results = self._multi_hop_search(entity_ids, filters, limit)
            result_relations.extend(multi_hop_results)

        return result_relations

    def _build_multi_hop_cte_query(
            self,
            base_filter: str,
            recursive_filter: str
    ) -> Any:
        """Build recursive CTE query for multi-hop graph traversal.

        Args:
            base_filter: SQL filter conditions for base query.
            recursive_filter: SQL filter conditions for recursive query.

        Returns:
            SQLAlchemy text query object.
        """
        # Base CTE query: Select direct relationships.
        base_cte = f"""
                SELECT
                    r.source_entity_id,
                    r.destination_entity_id,
                    r.relationship_type,
                    r.id as relation_id,
                    1 as hop_count,
                    CAST(
                        CONCAT(r.source_entity_id, ',', r.destination_entity_id) 
                        AS CHAR({constants.DEFAULT_PATH_STRING_LENGTH})
                    ) as path
                FROM {constants.TABLE_RELATIONSHIPS} r
                WHERE r.source_entity_id IN :entity_ids
                    AND {base_filter}
        """

        # Recursive CTE query: Join with previous paths.
        recursive_cte = f"""
                SELECT
                    p.source_entity_id,
                    r.destination_entity_id,
                    r.relationship_type,
                    r.id as relation_id,
                    p.hop_count + 1,
                    CAST(
                        CONCAT(p.path, ',', r.destination_entity_id) 
                        AS CHAR({constants.DEFAULT_PATH_STRING_LENGTH})
                    ) as path
                FROM path_search p
                JOIN {constants.TABLE_RELATIONSHIPS} r ON p.destination_entity_id = r.source_entity_id
                WHERE p.hop_count < {self.max_hops}
                    AND {recursive_filter}
                    AND FIND_IN_SET(r.destination_entity_id, p.path) = 0
        """

        # Final SELECT with entity name joins.
        final_select = f"""
            SELECT
                e1.name as source,
                p.source_entity_id as source_id,
                p.relationship_type as relationship,
                p.relation_id,
                e2.name as destination,
                p.destination_entity_id as destination_id,
                p.hop_count
            FROM (
                SELECT DISTINCT
                    source_entity_id,
                    destination_entity_id,
                    relationship_type,
                    relation_id,
                    hop_count
                FROM path_search
            ) p
            JOIN {constants.TABLE_ENTITIES} e1 ON p.source_entity_id = e1.id
            JOIN {constants.TABLE_ENTITIES} e2 ON p.destination_entity_id = e2.id
            ORDER BY p.hop_count ASC, p.source_entity_id, p.destination_entity_id
            LIMIT :limit
        """

        # Combine all parts into full CTE query.
        full_query = f"""
            WITH RECURSIVE path_search AS (
                {base_cte}

                UNION ALL

                {recursive_cte}
            )
            {final_select}
        """

        return text(full_query)

    def _multi_hop_search(
            self,
            entity_ids: List[str],
            filters: Dict[str, Any],
            limit: int
    ) -> List[Dict[str, Any]]:
        """Perform multi-hop graph search using recursive CTE.

        Args:
            entity_ids: List of seed entity IDs to start traversal from.
            filters: Dictionary containing user_id, agent_id, run_id.
            limit: Maximum number of results to return.

        Returns:
            List of dictionaries containing source, relationship, destination and their IDs.

        Note:
            Optimizations:
            - Prevents circular paths by tracking visited nodes with path string
            - Uses DISTINCT in final subquery (OceanBase doesn't support DISTINCT in recursive CTE)
            - Must use UNION ALL (OceanBase doesn't support UNION in recursive CTE)
            - Joins entity names only once at the end
            - Applies limit to final results
        """
        if not entity_ids:
            return []

        # Build dynamic filter conditions using helper method.
        filter_parts, params = self._build_filter_conditions(filters, prefix="r.")
        base_filter = " AND ".join(filter_parts)
        recursive_filter = base_filter  # Same filter for both base and recursive parts

        # Add additional params.
        params["entity_ids"] = tuple(entity_ids)
        params["limit"] = limit

        # Build optimized recursive CTE query.
        cte_query = self._build_multi_hop_cte_query(base_filter, recursive_filter)

        # Execute query
        with self.engine.connect() as conn:
            result = conn.execute(cte_query, params)
            rows = result.fetchall()

        # Format results to match graph_memory.py format
        formatted_results = []
        for row in rows:
            formatted_results.append({
                "source": row[0],  # source name
                "source_id": row[1],  # source_entity_id
                "relationship": row[2],  # relationship_type
                "relation_id": row[3],  # relation_id
                "destination": row[4],  # destination name
                "destination_id": row[5],  # destination_entity_id
            })

        return formatted_results

    def _get_delete_entities_from_search_output(
            self,
            search_output: List[Dict[str, Any]],
            data: str,
            filters: Dict[str, Any]
    ) -> List[Dict[str, str]]:
        """Get the entities to be deleted from the search output.

        Args:
            search_output: Search results from graph database.
            data: New input data to compare against.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            List of dictionaries containing source, destination, and relationship to delete.
        """
        search_output_string = format_entities(search_output)
        user_identity = self._build_user_identity(filters)
        system_prompt, user_prompt = self.graph_prompts.get_delete_relations_prompt(search_output_string, data, user_identity)

        _tools = [self.graph_tools_prompts.get_delete_tool()]
        if constants.is_structured_llm_provider(self.llm_provider):
            _tools = [self.graph_tools_prompts.get_delete_tool(structured=True)]

        memory_updates = self.llm.generate_response(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=_tools,
        )

        # Normalize to dict before access
        memory_updates = self._coerce_tool_response_to_dict(memory_updates)

        to_be_deleted = []
        for item in memory_updates.get("tool_calls", []):
            if item.get("name") == "delete_graph_memory":
                to_be_deleted.append(item.get("arguments"))

        to_be_deleted = self._remove_spaces_from_entities(to_be_deleted)
        logger.debug("Deleted relationships: %s", to_be_deleted)
        return to_be_deleted

    def _delete_entities(
            self,
            to_be_deleted: List[Dict[str, str]],
            filters: Dict[str, Any]
    ) -> List[Dict[str, int]]:
        """Delete the specified relationships from the graph.

        Args:
            to_be_deleted: List of relationships to delete with source, destination, relationship.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            List of dictionaries containing deleted_count for each deletion operation.
        """
        results = []

        for item in to_be_deleted:
            source = item["source"]
            destination = item["destination"]
            relationship = item["relationship"]

            # First, find the source and destination entities by name
            source_entities = self.client.get(
                table_name=constants.TABLE_ENTITIES,
                ids=None,
                output_column_name=["id", "name"],
                where_clause=[text(f"name = :source_name").bindparams(
                    bindparam("source_name", source)
                )]
            )

            dest_entities = self.client.get(
                table_name=constants.TABLE_ENTITIES,
                ids=None,
                output_column_name=["id", "name"],
                where_clause=[text(f"name = :dest_name").bindparams(
                    bindparam("dest_name", destination)
                )]
            )

            if not source_entities or not dest_entities:
                logger.warning(
                    "Could not find entities: source='%s', destination='%s'",
                    source,
                    destination
                )
                results.append({"deleted_count": 0})
                continue

            # Get entity IDs
            source_ids = [e[0] for e in source_entities]
            dest_ids = [e[0] for e in dest_entities]

            # Build where clause for relationship deletion
            where_clauses = [
                "relationship_type = :rel_type",
                "user_id = :user_id",
            ]
            params = {
                "rel_type": relationship,
                "user_id": filters["user_id"],
            }

            if filters.get("agent_id"):
                where_clauses.append("agent_id = :agent_id")
                params["agent_id"] = filters["agent_id"]
            if filters.get("run_id"):
                where_clauses.append("run_id = :run_id")
                params["run_id"] = filters["run_id"]

            # Add source and destination entity ID conditions.
            source_conditions = " OR ".join([
                f"source_entity_id = :src_id_{i}"
                for i in range(len(source_ids))
            ])
            dest_conditions = " OR ".join([
                f"destination_entity_id = :dest_id_{i}"
                for i in range(len(dest_ids))
            ])

            where_clauses.append(f"({source_conditions}) AND ({dest_conditions})")

            # Add entity ID parameters
            for i, src_id in enumerate(source_ids):
                params[f"src_id_{i}"] = src_id
            for i, dest_id in enumerate(dest_ids):
                params[f"dest_id_{i}"] = dest_id

            where_str = " AND ".join(where_clauses)
            where_clause = text(where_str).bindparams(**params)

            # Delete relationships using pyobvector delete method.
            try:
                delete_result = self.client.delete(
                    table_name=constants.TABLE_RELATIONSHIPS,
                    where_clause=where_clause
                )
                deleted_count = (
                    delete_result.rowcount
                    if hasattr(delete_result, "rowcount")
                    else 1
                )
                results.append({"deleted_count": deleted_count})
            except Exception as e:
                logger.warning("Error deleting relationship: %s", e)
                results.append({"deleted_count": 0})

        return results

    def _add_entities(
            self,
            to_be_added: List[Dict[str, str]],
            filters: Dict[str, Any],
            entity_type_map: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Add new entities and relationships to the graph.

        Args:
            to_be_added: List of relationships to add with source, destination, relationship.
            filters: Dictionary containing user_id, agent_id, run_id.
            entity_type_map: Mapping of entity names to types.

        Returns:
            List of dictionaries containing added source, relationship, and target.
        """
        results = []

        for item in to_be_added:
            source = item["source"]
            destination = item["destination"]
            relationship = item["relationship"]

            source_embedding = self.embedding_model.embed(source)
            dest_embedding = self.embedding_model.embed(destination)

            # Search for existing similar nodes.
            source_node = self._search_source_node(source_embedding, filters,
                                                   threshold=constants.DEFAULT_SIMILARITY_THRESHOLD, limit=1)
            dest_node = self._search_destination_node(dest_embedding, filters,
                                                      threshold=constants.DEFAULT_SIMILARITY_THRESHOLD, limit=1)

            # Get or create source entity
            if source_node:
                source_id = source_node["id"]
                self._update_entity_mentions(source_id)
            else:
                source_id = self._create_entity(source, entity_type_map.get(source, "entity"),
                                                source_embedding, filters)

            # Get or create destination entity
            if dest_node:
                dest_id = dest_node["id"]
                self._update_entity_mentions(dest_id)
            else:
                dest_id = self._create_entity(destination, entity_type_map.get(destination, "entity"),
                                              dest_embedding, filters)

            # Create or update relationship
            rel_result = self._create_or_update_relationship(source_id, dest_id, relationship, filters)
            results.append(rel_result)

        return results

    def _search_node(
            self,
            name: Optional[str],
            embedding: List[float],
            filters: Dict[str, Any],
            threshold: float = None,
            limit: int = 10
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Search for a node by embedding similarity within threshold.

        Args:
            name: Node name (not currently used, kept for compatibility).
            embedding: Vector embedding to search with.
            filters: Dictionary containing user_id, agent_id, run_id.
            threshold: Distance threshold for filtering results.
                      Defaults to constants.DEFAULT_SIMILARITY_THRESHOLD if None.
            limit: Maximum number of results to return. Defaults to 10.

        Returns:
            If limit==1: Single dict with id, name, distance, or None if no match.
            If limit>1: List of dicts with id, name, distance, or None if no match.
        """
        if threshold is None:
            threshold = constants.DEFAULT_SIMILARITY_THRESHOLD

        # Create Table object to access columns for WHERE clause.
        table = Table(constants.TABLE_ENTITIES, self.metadata, autoload_with=self.engine)
        vec_str = "[" + ",".join([str(np.float32(v)) for v in embedding]) + "]"
        distance_expr = l2_distance(table.c.embedding, vec_str)
        where_clause = [distance_expr < threshold]

        results = self.client.ann_search(
            table_name=constants.TABLE_ENTITIES,
            vec_data=embedding,
            vec_column_name="embedding",
            distance_func=l2_distance,
            with_dist=True,
            topk=limit,
            output_column_names=["id", "name"],
            where_clause=where_clause,
        )

        rows = results.fetchall()
        if rows:
            if limit == 1:
                row = rows[0]
                # Row format: (id, name, distance)
                entity_id, entity_name = row[0], row[1]
                distance = row[-1]  # Distance is always the last column

                return {"id": entity_id, "name": entity_name, "distance": distance}
            else:
                return [{"id": row[0], "name": row[1], "distance": row[-1]} for row in rows]

        return None

    def _create_entity(
            self,
            name: str,
            entity_type: str,
            embedding: List[float],
            filters: Dict[str, Any]
    ) -> str:
        """Create a new entity in the graph.

        Args:
            name: Entity name.
            entity_type: Type of the entity.
            embedding: Vector embedding of the entity.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            UUID of the created entity.
        """
        entity_id = str(uuid.uuid4())

        # Prepare data for insertion using pyobvector API
        record = {
            "id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "embedding": embedding,
            "mentions": 1,
        }

        # Use pyobvector upsert method
        self.client.upsert(
            table_name=constants.TABLE_ENTITIES,
            data=[record],
        )

        logger.debug("Created entity: %s with id: %s", name, entity_id)
        return entity_id

    def _update_entity_mentions(self, entity_id: str) -> None:
        """Update the mentions count for an entity.

        Args:
            entity_id: UUID of the entity to update.
        """
        results = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=[entity_id],
            output_column_name=["id", "name", "entity_type", "embedding", "mentions"],
        )

        rows = results.fetchall()
        if not rows:
            logger.warning("Entity %s not found for mention update", entity_id)
            return

        row = rows[0]
        # Unpack row data.
        entity_id, name, entity_type, embedding, mentions = row

        # Increment mentions and update using upsert.
        updated_record = {
            "id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "embedding": embedding,
            "mentions": mentions + 1,
        }

        self.client.upsert(
            table_name=constants.TABLE_ENTITIES,
            data=[updated_record],
        )

    def _create_or_update_relationship(
            self,
            source_id: str,
            dest_id: str,
            relationship_type: str,
            filters: Dict[str, Any]
    ) -> Dict[str, str]:
        """Create or update a relationship between two entities.

        Args:
            source_id: UUID of the source entity.
            dest_id: UUID of the destination entity.
            relationship_type: Type of the relationship.
            filters: Dictionary containing user_id, agent_id, run_id.

        Returns:
            Dictionary containing source, relationship, and target names.
        """
        # First, check if relationship already exists
        where_clause, params = self._build_where_clause_with_filters(filters)

        # Add relationship-specific conditions to the where clause
        additional_conditions = " AND source_entity_id = :source_id AND destination_entity_id = :dest_id AND relationship_type = :rel_type"

        # Rebuild the where clause with additional conditions
        where_str = str(where_clause[0].text) + additional_conditions

        params.update({
            "source_id": source_id,
            "dest_id": dest_id,
            "rel_type": relationship_type
        })

        where_clause_with_params = text(where_str).bindparams(**params)

        # Check if relationship exists
        existing_relationships = self.client.get(
            table_name=constants.TABLE_RELATIONSHIPS,
            ids=None,
            output_column_name=["id", "mentions"],
            where_clause=[where_clause_with_params]
        )

        existing_rows = existing_relationships.fetchall()
        if existing_rows:
            # Relationship exists, update mentions only
            existing_row = existing_rows[0]
            existing_id = existing_row[0]
            new_mentions = existing_row[1] + 1

            self.client.update(
                table_name=constants.TABLE_RELATIONSHIPS,
                values_clause=[{"mentions": new_mentions}],
                where_clause=[text(f"id = '{existing_id}'")],
            )
        else:
            # Relationship doesn't exist, create new one
            new_record = {
                "id": str(uuid.uuid4()),
                "source_entity_id": source_id,
                "relationship_type": relationship_type,
                "destination_entity_id": dest_id,
                "user_id": filters["user_id"],
                "agent_id": filters.get("agent_id"),
                "run_id": filters.get("run_id"),
                "mentions": 1,
            }

            self.client.insert(
                table_name=constants.TABLE_RELATIONSHIPS,
                data=[new_record],
            )

        # Get the names for return value using pyobvector get method
        # First get the entities
        source_entity = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=[source_id],
            output_column_name=["id", "name"]
        ).fetchone()

        dest_entity = self.client.get(
            table_name=constants.TABLE_ENTITIES,
            ids=[dest_id],
            output_column_name=["id", "name"]
        ).fetchone()

        return {
            "source": source_entity[1] if source_entity else None,
            "relationship": relationship_type,
            "target": dest_entity[1] if dest_entity else None,
        }

    def _remove_spaces_from_entities(self, entity_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Clean entity names by replacing spaces with underscores.

        Args:
            entity_list: List of dictionaries containing source, destination, relationship.

        Returns:
            Cleaned entity list with spaces replaced by underscores and lowercased.
        """
        for item in entity_list:
            item["source"] = item["source"].lower().replace(" ", "_")
            item["relationship"] = item["relationship"].lower().replace(" ", "_")
            item["destination"] = item["destination"].lower().replace(" ", "_")
        return entity_list

    def _search_source_node(
            self,
            source_embedding: List[float],
            filters: Dict[str, Any],
            threshold: float = None,
            limit: int = 10
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Search for a source node by embedding similarity (compatibility method).

        Args:
            source_embedding: Vector embedding to search with.
            filters: Dictionary containing user_id, agent_id, run_id.
            threshold: Distance threshold for filtering results.
                      Defaults to constants.DEFAULT_SIMILARITY_THRESHOLD if None.
            limit: Maximum number of results to return. Defaults to 10.

        Returns:
            Search results from _search_node method.
        """
        return self._search_node("source", source_embedding, filters, threshold, limit)

    def _search_destination_node(
            self,
            destination_embedding: List[float],
            filters: Dict[str, Any],
            threshold: float = None,
            limit: int = 10
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Search for a destination node by embedding similarity (compatibility method).

        Args:
            destination_embedding: Vector embedding to search with.
            filters: Dictionary containing user_id, agent_id, run_id.
            threshold: Distance threshold for filtering results.
                      Defaults to constants.DEFAULT_SIMILARITY_THRESHOLD if None.
            limit: Maximum number of results to return. Defaults to 10.

        Returns:
            Search results from _search_node method.
        """
        return self._search_node("destination", destination_embedding, filters, threshold, limit)

    def reset(self) -> None:
        """Reset the graph by clearing all nodes and relationships.

        This method drops both entities and relationships tables and recreates them.
        """
        logger.warning("Clearing graph...")

        # Use pyobvector API to drop tables
        if self.client.check_table_exists(constants.TABLE_RELATIONSHIPS):
            self.client.drop_table_if_exist(constants.TABLE_RELATIONSHIPS)
            logger.info("Dropped %s table", constants.TABLE_RELATIONSHIPS)

        if self.client.check_table_exists(constants.TABLE_ENTITIES):
            self.client.drop_table_if_exist(constants.TABLE_ENTITIES)
            logger.info("Dropped %s table", constants.TABLE_ENTITIES)

        # Recreate tables
        self._create_tables()

        logger.info("Graph reset completed")