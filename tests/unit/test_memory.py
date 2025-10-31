"""
Basic tests for powermem

This module contains basic unit tests for the memory system.
"""

import pytest
from powermem import Memory
from powermem.core.base import MemoryBase


class TestMemory:
    """Test cases for Memory class."""
    
    def test_memory_initialization(self):
        """Test memory initialization."""
        memory = Memory()
        assert isinstance(memory, MemoryBase)
    
    def test_add_memory(self):
        """Test adding a memory."""
        memory = Memory()
        result = memory.add("Test memory content", user_id="test_user")
        
        assert "id" in result
        assert result["content"] == "Test memory content"
        assert result["user_id"] == "test_user"
    
    def test_search_memories(self):
        """Test searching memories."""
        memory = Memory()
        
        # Add some test memories
        memory.add("User likes coffee", user_id="test_user")
        memory.add("User prefers Python", user_id="test_user")
        
        # Search for memories
        results = memory.search("user preferences", user_id="test_user")
        
        assert isinstance(results, list)
        assert len(results) >= 0
    
    def test_get_memory(self):
        """Test getting a specific memory."""
        memory = Memory()
        
        # Add a memory
        result = memory.add("Test memory", user_id="test_user")
        memory_id = result["id"]
        
        # Get the memory
        retrieved = memory.get(memory_id, user_id="test_user")
        
        assert retrieved is not None
        assert retrieved["id"] == memory_id
        assert retrieved["content"] == "Test memory"
    
    def test_update_memory(self):
        """Test updating a memory."""
        memory = Memory()
        
        # Add a memory
        result = memory.add("Original content", user_id="test_user")
        memory_id = result["id"]
        
        # Update the memory
        updated = memory.update(memory_id, "Updated content", user_id="test_user")
        
        assert updated["content"] == "Updated content"
    
    def test_delete_memory(self):
        """Test deleting a memory."""
        memory = Memory()
        
        # Add a memory
        result = memory.add("To be deleted", user_id="test_user")
        memory_id = result["id"]
        
        # Delete the memory
        deleted = memory.delete(memory_id, user_id="test_user")
        
        assert deleted is True
        
        # Verify it's deleted
        retrieved = memory.get(memory_id, user_id="test_user")
        assert retrieved is None
    
    def test_clear_memories(self):
        """Test clearing memories."""
        memory = Memory()
        
        # Add some memories
        memory.add("Memory 1", user_id="test_user")
        memory.add("Memory 2", user_id="test_user")
        
        # Clear memories
        cleared = memory.delete_all(user_id="test_user")
        
        assert cleared is True
        
        # Verify memories are cleared
        results = memory.search("", user_id="test_user")
        assert len(results) == 0
