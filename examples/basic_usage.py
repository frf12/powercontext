"""
Basic usage example for powermem

This example demonstrates basic memory operations using configuration from configs/.env file.
Make sure to create a configs/.env file with your configuration.
"""

import os
from dotenv import load_dotenv
from mem import Memory

def load_config():
    """Load configuration from configs/.env"""
    config_path = os.path.join(os.path.dirname(__file__), "configs", ".env")
    
    # Load environment variables from .env file
    load_dotenv(config_path)
    
    # Build configuration dictionary from environment variables
    config = {
        'database': {
            'provider': os.getenv('DATABASE_PROVIDER', 'sqlite'),
            'config': {
                'database_path': os.getenv('DATABASE_PATH', './data/powermem_dev.db'),
                'enable_wal': os.getenv('DATABASE_ENABLE_WAL', 'true').lower() == 'true',
                'timeout': int(os.getenv('DATABASE_TIMEOUT', '30'))
            }
        },
        'llm': {
            'provider': os.getenv('LLM_PROVIDER', 'qwen'),
            'config': {
                'api_key': os.getenv('LLM_API_KEY'),
                'model': os.getenv('LLM_MODEL', 'qwen-plus'),
                'temperature': float(os.getenv('LLM_TEMPERATURE', '0.7')),
                'max_tokens': int(os.getenv('LLM_MAX_TOKENS', '1000')),
                'top_p': float(os.getenv('LLM_TOP_P', '0.8')),
                'top_k': int(os.getenv('LLM_TOP_K', '50')),
                'dashscope_base_url': os.getenv('LLM_BASE_URL', 'https://dashscope.aliyuncs.com/api/v1'),
                'enable_search': os.getenv('LLM_ENABLE_SEARCH', 'false').lower() == 'true'
            }
        },
        'embedding': {
            'provider': os.getenv('EMBEDDING_PROVIDER', 'qwen'),
            'config': {
                'api_key': os.getenv('EMBEDDING_API_KEY'),
                'model': os.getenv('EMBEDDING_MODEL', 'text-embedding-v4'),
                'embedding_dims': int(os.getenv('EMBEDDING_DIMS', '1536'))
            }
        },
        'intelligent_memory': {
            'enabled': os.getenv('INTELLIGENT_MEMORY_ENABLED', 'true').lower() == 'true',
            'initial_retention': float(os.getenv('INTELLIGENT_MEMORY_INITIAL_RETENTION', '1.0')),
            'decay_rate': float(os.getenv('INTELLIGENT_MEMORY_DECAY_RATE', '0.1')),
            'reinforcement_factor': float(os.getenv('INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR', '0.3')),
            'working_threshold': float(os.getenv('INTELLIGENT_MEMORY_WORKING_THRESHOLD', '0.3')),
            'short_term_threshold': float(os.getenv('INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD', '0.6')),
            'long_term_threshold': float(os.getenv('INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD', '0.8'))
        },
        'telemetry': {
            'enable_telemetry': os.getenv('TELEMETRY_ENABLED', 'false').lower() == 'true',
            'telemetry_endpoint': os.getenv('TELEMETRY_ENDPOINT', 'https://telemetry.powermem.ai'),
            'telemetry_api_key': os.getenv('TELEMETRY_API_KEY'),
            'telemetry_batch_size': int(os.getenv('TELEMETRY_BATCH_SIZE', '100')),
            'telemetry_flush_interval': int(os.getenv('TELEMETRY_FLUSH_INTERVAL', '30'))
        },
        'audit': {
            'enabled': os.getenv('AUDIT_ENABLED', 'true').lower() == 'true',
            'log_file': os.getenv('AUDIT_LOG_FILE', './logs/audit.log'),
            'log_level': os.getenv('AUDIT_LOG_LEVEL', 'INFO'),
            'retention_days': int(os.getenv('AUDIT_RETENTION_DAYS', '90'))
        },
        'logging': {
            'level': os.getenv('LOGGING_LEVEL', 'DEBUG'),
            'format': os.getenv('LOGGING_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            'file': os.getenv('LOGGING_FILE', './logs/powermem.log')
        }
    }
    
    return config

def main():
    """Basic usage example."""
    # Load configuration
    config = load_config()
    
    # Initialize memory with configuration
    memory = Memory(config=config)
    
    # Add some memories
    memory.add("User likes coffee", user_id="user123")
    memory.add("User prefers Python over Java", user_id="user123")
    memory.add("User works as a software engineer", user_id="user123")
    
    # Search memories
    results = memory.search("user preferences", user_id="user123")
    print("Search results:")
    for result in results:
        print(f"- {result['content']}")
    
    # Get all memories
    all_memories = memory.get_all(user_id="user123")
    print(f"\nTotal memories: {len(all_memories)}")

if __name__ == "__main__":
    main()
