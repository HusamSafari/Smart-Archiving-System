"""
Topic Manager Module

Handles topic configuration and user state management:
- Load/save topics from topics.json
- Track user-specific current topics
- Thread-safe file operations
"""

import json
import logging
import asyncio
from typing import Optional, Dict, List
from pathlib import Path

logger = logging.getLogger(__name__)


class TopicManager:
    """Manages topics and user state"""
    
    def __init__(
        self,
        topics_file: str = "topics.json",
        user_state_file: str = "user_topics.json",
        default_folder_id: Optional[str] = None
    ):
        """
        Initialize Topic Manager
        
        Args:
            topics_file: Path to topics configuration file
            user_state_file: Path to user state file
            default_folder_id: Default Google Drive folder ID
        """
        self.topics_file = Path(topics_file)
        self.user_state_file = Path(user_state_file)
        self.default_folder_id = default_folder_id
        
        # In-memory caches
        self.topics: Dict[str, dict] = {}
        self.user_states: Dict[int, str] = {}  # user_id -> topic_name
        
        # File locks for thread safety
        self.topics_lock = asyncio.Lock()
        self.user_state_lock = asyncio.Lock()
        
        # Load initial data
        self._load_topics()
        self._load_user_states()
    
    def _load_topics(self) -> None:
        """Load topics from JSON file"""
        try:
            if self.topics_file.exists():
                with open(self.topics_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Support both root-level array and wrapped {"topics": [...]}
                    topics_list = data if isinstance(data, list) else data.get('topics', [])
                    self.topics = {
                        topic['name']: topic
                        for topic in topics_list
                    }
                    
                    logger.info(f"Loaded {len(self.topics)} topics from {self.topics_file}")
            else:
                logger.warning(f"Topics file {self.topics_file} not found, creating empty topics")
                self.topics = {}
                self._save_topics()
        except Exception as e:
            logger.error(f"Error loading topics: {e}")
            self.topics = {}
    
    def _save_topics(self) -> None:
        """Save topics to JSON file (root-level array)"""
        try:
            topics_list = list(self.topics.values())
            with open(self.topics_file, 'w', encoding='utf-8') as f:
                json.dump(topics_list, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(self.topics)} topics to {self.topics_file}")
        except Exception as e:
            logger.error(f"Error saving topics: {e}")
    
    def _load_user_states(self) -> None:
        """Load user states from JSON file"""
        try:
            if self.user_state_file.exists():
                with open(self.user_state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Convert string keys back to integers
                    self.user_states = {
                        int(user_id): topic_name
                        for user_id, topic_name in data.items()
                    }
                    
                    logger.info(f"Loaded {len(self.user_states)} user states from {self.user_state_file}")
            else:
                logger.info(f"User state file {self.user_state_file} not found, creating empty state")
                self.user_states = {}
                self._save_user_states()
        except Exception as e:
            logger.error(f"Error loading user states: {e}")
            self.user_states = {}
    
    def _save_user_states(self) -> None:
        """Save user states to JSON file"""
        try:
            self.user_state_file.parent.mkdir(parents=True, exist_ok=True)
            # Convert integer keys to strings for JSON
            data = {
                str(user_id): topic_name
                for user_id, topic_name in self.user_states.items()
            }
            
            with open(self.user_state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved {len(self.user_states)} user states to {self.user_state_file}")
        except Exception as e:
            logger.error(f"Error saving user states: {e}")
    
    async def get_topic(self, topic_name: str) -> Optional[dict]:
        """
        Get topic by name
        
        Args:
            topic_name: Name of the topic
            
        Returns:
            Topic dict if found, None otherwise
        """
        async with self.topics_lock:
            return self.topics.get(topic_name)
    
    async def get_all_topics(self) -> List[dict]:
        """
        Get all topics
        
        Returns:
            List of all topic dicts
        """
        async with self.topics_lock:
            return list(self.topics.values())
    
    async def set_user_topic(self, user_id: int, topic_name: str) -> bool:
        """
        Set current topic for a user
        
        Args:
            user_id: Telegram user ID
            topic_name: Name of the topic to set
            
        Returns:
            True if successful, False if topic doesn't exist
        """
        # Validate topic exists
        topic = await self.get_topic(topic_name)
        if not topic:
            logger.warning(f"Attempted to set non-existent topic: {topic_name}")
            return False
        
        async with self.user_state_lock:
            self.user_states[user_id] = topic_name
            self._save_user_states()
        
        logger.info(f"User {user_id} topic set to: {topic_name}")
        return True
    
    async def get_user_topic(self, user_id: int) -> Optional[str]:
        """
        Get current topic for a user
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Topic name if set, None otherwise
        """
        async with self.user_state_lock:
            return self.user_states.get(user_id)
    
    async def get_folder_id_for_user(self, user_id: int) -> str:
        """
        Get Google Drive folder ID for user's current topic
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Folder ID (uses default if no topic set)
        """
        topic_name = await self.get_user_topic(user_id)
        
        if topic_name:
            topic = await self.get_topic(topic_name)
            if topic and 'drive_folder_id' in topic:
                return topic['drive_folder_id']
        
        # Fallback to default folder
        if self.default_folder_id:
            logger.info(f"Using default folder for user {user_id}")
            return self.default_folder_id
        
        raise ValueError("No topic set and no default folder configured")
    
    async def get_hashtag_for_user(self, user_id: int) -> Optional[str]:
        """
        Get hashtag for user's current topic
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Hashtag if topic set, None otherwise
        """
        topic_name = await self.get_user_topic(user_id)
        
        if topic_name:
            topic = await self.get_topic(topic_name)
            if topic:
                return topic.get('hashtag')
        
        return None
    
    async def clear_user_topic(self, user_id: int) -> None:
        """
        Clear current topic for a user
        
        Args:
            user_id: Telegram user ID
        """
        async with self.user_state_lock:
            if user_id in self.user_states:
                del self.user_states[user_id]
                self._save_user_states()
                logger.info(f"Cleared topic for user {user_id}")
    
    async def add_topic(
        self,
        name: str,
        drive_folder_id: str,
        hashtag: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        """
        Add a new topic (for future admin features)
        
        Args:
            name: Topic name
            drive_folder_id: Google Drive folder ID
            hashtag: Topic hashtag (optional)
            description: Topic description (optional)
            
        Returns:
            True if successful, False if topic already exists
        """
        async with self.topics_lock:
            if name in self.topics:
                logger.warning(f"Topic {name} already exists")
                return False
            
            topic = {
                'name': name,
                'drive_folder_id': drive_folder_id,
                'hashtag': hashtag or f"#{name}",
                'description': description or f"{name} related content"
            }
            
            self.topics[name] = topic
            self._save_topics()
            
            logger.info(f"Added new topic: {name}")
            return True
