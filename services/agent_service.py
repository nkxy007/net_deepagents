"""
Agent service for managing network deep agent interactions.
Provides a service layer between UI and the net_deepagent backend.
"""

import asyncio
import traceback
import logging
from typing import Optional, AsyncIterator, Dict, Any, List, Tuple
from pathlib import Path
import sys

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))
import creds

from net_deepagent import (
    create_network_agent,
    set_user_clarification_callback,
    AVAILABLE_MODELS
)
from utils.session_manager import SessionManager
from utils.file_handler import FileHandler
from utils.session_persistence import SessionPersistence
from a2a_capability.middleware import A2AHTTPMiddleware


class AgentService:
    """
    Service layer for managing network deep agent operations.
    Handles session management, streaming, file uploads, and clarification flow.
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        """Singleton pattern to ensure only one instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize AgentService (singleton)."""
        if not self._initialized:
            logger.info("Initializing AgentService singleton")
            self.agent = None
            self.file_handler = FileHandler()
            self.session_persistence = SessionPersistence()
            self.session_manager = SessionManager(
                persistence_handler=self.session_persistence,
                auto_save=True
            )
            self.mcp_server_url = "http://localhost:8000/mcp"
            self.default_main_model = "gpt-5-mini"
            self.default_subagent_model = "gpt-5-mini"
            self.default_design_model = "gpt-5.1"
            
            # Initialize A2A middleware
            self.a2a_middleware = A2AHTTPMiddleware()
            self.a2a_tools = []
            
            AgentService._initialized = True
            logger.info("AgentService initialized successfully")

    async def initialize(
        self,
        mcp_server_url: Optional[str] = None,
        main_model: Optional[str] = None,
        subagent_model: Optional[str] = None,
        design_model: Optional[str] = None,
    ):
        """
        Initialize the agent with specified configuration.

        Args:
            mcp_server_url: Optional MCP server URL
            main_model: Optional main model name
            subagent_model: Optional subagent model name
            design_model: Optional design model name
        """
        logger.info("Starting agent initialization")
        logger.debug(f"MCP URL: {mcp_server_url or self.mcp_server_url}")
        logger.debug(f"Main model: {main_model or self.default_main_model}")

        if mcp_server_url:
            self.mcp_server_url = mcp_server_url
        if main_model:
            self.default_main_model = main_model
        if subagent_model:
            self.default_subagent_model = subagent_model
        if design_model:
            self.default_design_model = design_model

        # Register A2A agents from registry
        logger.info("Registering A2A agents...")
        try:
            import a2a_capability
            registry_path = Path(a2a_capability.__file__).parent / "agents_registry.json"
        except ImportError:
            registry_path = Path.cwd() / "a2a_capability" / "agents_registry.json"
        
        logger.info(f"Looking for A2A registry at: {registry_path}")
        logger.info(f"Registry exists: {registry_path.exists()}")
        
        if registry_path.exists():
            logger.info(f"Loading A2A registry from {registry_path}")
            try:
                await self.a2a_middleware.register_agents_from_file(str(registry_path))
                self.a2a_tools = self.a2a_middleware.tools
                logger.info(f"✅ Registered {len(self.a2a_tools)} A2A tools")
                logger.info(f"✅ Remote agents registered: {list(self.a2a_middleware.remote_agents.keys())}")
            except Exception as e:
                logger.error(f"❌ Failed to register A2A agents: {e}", exc_info=True)
                self.a2a_tools = []
        else:
            logger.warning(f"⚠️ A2A registry not found at {registry_path}")
            self.a2a_tools = []

        # Create agent instance
        logger.info("Creating network agent...")
        try:
            logger.debug("Calling create_network_agent()...")
            self.agent = await create_network_agent(
                mcp_server_url=self.mcp_server_url,
                main_model_name=self.default_main_model,
                subagent_model_name=self.default_subagent_model,
                design_model_name=self.default_design_model,
                extra_tools=self.a2a_tools,
            )
            logger.info(f"Network agent created successfully! Agent type: {type(self.agent)}")
            logger.info(f"Agent object: {self.agent}")
            logger.debug(f"Agent has astream method: {hasattr(self.agent, 'astream')}")
        except Exception as e:
            logger.error(f"Failed to create network agent: {e}", exc_info=True)
            raise

        # Set up clarification callback
        logger.debug("Setting up clarification callback")
        set_user_clarification_callback(self._clarification_callback)
        logger.info("Agent initialization complete")

    def _clarification_callback(self, question: str, intention: str = "") -> str:
        """
        Callback for handling clarification requests from agent.
        This is called by the agent when it needs user input.

        Args:
            question: The clarification question
            intention: Optional intention behind the question

        Returns:
            User's response
        """
        # This will be replaced by UI integration
        # For now, queue the question and wait for response
        # In UI mode, this will be handled asynchronously
        print(f"[AgentService] Clarification requested: {question}")
        # Return placeholder - will be replaced by actual UI mechanism
        return "Please provide clarification through the UI."

    async def stream_response(
        self,
        message: str,
        session_id: str,
        main_model: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Stream agent response for a user message.

        Args:
            message: User message
            session_id: Session identifier
            main_model: Optional model override for this request

        Yields:
            Dict chunks from agent stream with metadata
        """
        logger.info(f"Stream response started for session {session_id}")
        logger.debug(f"Message: {message[:100]}...")

        if not self.agent:
            logger.error("AgentService not initialized!")
            raise RuntimeError("AgentService not initialized. Call initialize() first.")

        # Ensure session exists
        if not self.session_manager.session_exists(session_id):
            logger.debug(f"Creating new session: {session_id}")
            self.session_manager.create_session(session_id)

        # Add user message to history
        logger.debug("Adding user message to history")
        self.session_manager.add_message(session_id, "user", message)

        # Get full conversation history for context
        conversation_history = self.session_manager.get_messages_for_agent(session_id)
        logger.info(f"📜 Conversation history length: {len(conversation_history)} messages")
        logger.debug(f"History preview: {[msg['role'] for msg in conversation_history]}")

        # Log stream event
        logger.debug("About to add stream log...")
        self.session_manager.add_stream_log(
            session_id,
            "Message Received",
            f"Processing with {len(conversation_history)} messages in context: {message[:100]}..."
        )
        logger.debug("Stream log added successfully")

        try:
            logger.info("=== ENTERING TRY BLOCK ===")
            logger.info(f"Agent object: {self.agent}")
            logger.info(f"Agent type: {type(self.agent)}")
            logger.info("About to call agent.astream()...")

            # Stream agent response
            full_response = ""
            chunk_count = 0

            # Prepare the input with FULL conversation history for context continuity
            agent_input = {"messages": conversation_history}
            logger.info(f"📨 Sending {len(conversation_history)} messages to agent (with full context)")
            logger.debug(f"Agent input structure: {[{k: type(v).__name__} for k, v in agent_input.items()]}")
            logger.info("=== CALLING agent.astream() NOW ===")

            async for chunk in self.agent.astream(agent_input):
                logger.info(f"=== INSIDE ASYNC FOR LOOP - Got first chunk! ===")
                chunk_count += 1
                logger.debug(f"Received chunk #{chunk_count}: {type(chunk)}")
                # Process different chunk types
                chunk_type = None
                content = None
                metadata = {}

                # Model response
                if "messages" in chunk.get("model", ""):
                    chunk_type = "model"
                    msg = chunk["model"]["messages"][-1]
                    content_raw = msg.content if hasattr(msg, 'content') else str(msg)
                    if isinstance(content_raw, list):
                        content = "".join([
                            block.get("text", "") if isinstance(block, dict) and block.get("type") == "text" 
                            else str(block) if isinstance(block, str) else ""
                            for block in content_raw
                        ])
                    else:
                        content = str(content_raw)
                    full_response += content
                    logger.debug(f"Model response chunk: {content[:50]}...")

                    # Log model response
                    self.session_manager.add_stream_log(
                        session_id,
                        "Model Response",
                        f"Generated: {content[:50]}...",
                        {"chunk_size": len(content)}
                    )

                # Tool call
                elif "messages" in chunk.get("tools", ""):
                    chunk_type = "tool"
                    msg = chunk["tools"]["messages"][-1]
                    tool_name = getattr(msg, 'name', 'unknown')
                    content = str(msg)
                    logger.info(f"Tool call: {tool_name}")

                    # Log tool call
                    self.session_manager.add_stream_log(
                        session_id,
                        "Tool Call",
                        f"Executing: {tool_name}",
                        {"tool": tool_name}
                    )

                    metadata["tool_name"] = tool_name

                # Yield processed chunk
                logger.debug(f"Yielding chunk type: {chunk_type}")
                yield {
                    "type": chunk_type,
                    "content": content,
                    "metadata": metadata,
                    "raw_chunk": chunk,
                    "session_id": session_id,
                }

            logger.info(f"Streaming complete. Total chunks: {chunk_count}, Response length: {len(full_response)}")

            # Add assistant response to history
            self.session_manager.add_message(session_id, "assistant", full_response)

            # Log completion
            self.session_manager.add_stream_log(
                session_id,
                "Response Complete",
                f"Total length: {len(full_response)} chars"
            )

            # Extract artifacts if any
            logger.debug("Extracting artifacts...")
            self._extract_artifacts(session_id, full_response)

        except Exception as e:
            error_msg = f"Error during agent streaming: {str(e)}"
            traceback_str = traceback.format_exc()
            logger.error(error_msg, exc_info=True)

            # Log error
            self.session_manager.add_error(session_id, error_msg, traceback_str)
            self.session_manager.add_stream_log(
                session_id,
                "Error",
                error_msg
            )

            # Yield error chunk
            yield {
                "type": "error",
                "content": error_msg,
                "metadata": {"traceback": traceback_str},
                "session_id": session_id,
            }

    def _extract_artifacts(self, session_id: str, response: str):
        """
        Extract artifacts from response (code blocks, configs, etc.).

        Args:
            session_id: Session identifier
            response: Full response text
        """
        # Simple artifact extraction (can be enhanced)
        # Look for code blocks, configuration snippets, etc.

        # Extract code blocks
        import re
        code_blocks = re.findall(r'```(\w+)?\n(.*?)```', response, re.DOTALL)

        for i, (lang, code) in enumerate(code_blocks):
            artifact = {
                "name": f"Code Block {i + 1}",
                "type": f"code/{lang}" if lang else "code",
                "content": code.strip(),
                "language": lang or "text",
            }
            self.session_manager.add_artifact(session_id, artifact)

    async def process_message(
        self,
        message: str,
        session_id: str,
        main_model: Optional[str] = None,
    ) -> str:
        """
        Process message and return full response (non-streaming).

        Args:
            message: User message
            session_id: Session identifier
            main_model: Optional model override

        Returns:
            Complete agent response
        """
        full_response = ""
        async for chunk in self.stream_response(message, session_id, main_model):
            if chunk["type"] == "model" and chunk["content"]:
                full_response += chunk["content"]

        return full_response

    async def upload_file(
        self,
        file_data: Any,
        filename: str,
        file_type: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """
        Handle file upload.

        Args:
            file_data: Binary file data
            filename: Original filename
            file_type: 'document' or 'diagram'
            session_id: Session identifier

        Returns:
            Dict with success status, message, and file info
        """
        # Save file using file handler
        success, error_msg, file_path = self.file_handler.save_file(
            file_data, filename, session_id, file_type
        )

        if not success:
            return {
                "success": False,
                "message": error_msg,
                "file_path": None
            }

        # Get file info
        file_info = self.file_handler.get_file_info(file_path)

        # Record in session
        self.session_manager.add_uploaded_file(session_id, file_type, file_info)

        # Log upload
        self.session_manager.add_stream_log(
            session_id,
            "File Uploaded",
            f"{file_type.capitalize()}: {filename}",
            {"file_path": str(file_path)}
        )

        return {
            "success": True,
            "message": f"{file_type.capitalize()} uploaded successfully",
            "file_path": str(file_path),
            "file_info": file_info
        }

    def get_session_history(self, session_id: str) -> List[Dict]:
        """
        Get chat history for session.

        Args:
            session_id: Session identifier

        Returns:
            List of messages
        """
        return self.session_manager.get_messages(session_id)

    def get_session_artifacts(self, session_id: str) -> List[Dict]:
        """
        Get artifacts for session.

        Args:
            session_id: Session identifier

        Returns:
            List of artifacts
        """
        return self.session_manager.get_artifacts(session_id)

    def get_stream_logs(self, session_id: str, limit: Optional[int] = None) -> List[Dict]:
        """
        Get stream logs for session.

        Args:
            session_id: Session identifier
            limit: Optional limit on number of logs

        Returns:
            List of log entries
        """
        return self.session_manager.get_stream_logs(session_id, limit)

    def get_errors(self, session_id: str) -> List[Dict]:
        """
        Get errors for session.

        Args:
            session_id: Session identifier

        Returns:
            List of errors
        """
        return self.session_manager.get_errors(session_id)

    def get_uploaded_files(self, session_id: str) -> Dict[str, List]:
        """
        Get uploaded files for session.

        Args:
            session_id: Session identifier

        Returns:
            Dict with documents and diagrams lists
        """
        return self.session_manager.get_uploaded_files(session_id)

    def clear_session(self, session_id: str):
        """
        Clear session data.

        Args:
            session_id: Session identifier
        """
        # Clear session manager data
        self.session_manager.clear_session(session_id)

        # Clear uploaded files
        self.file_handler.clear_session_files(session_id)

    def get_available_models(self) -> List[str]:
        """
        Get list of available model names.

        Returns:
            List of model names
        """
        return list(AVAILABLE_MODELS.keys())

    def get_default_models(self) -> Dict[str, str]:
        """
        Get default model configuration.

        Returns:
            Dict with default model names
        """
        return {
            "main_model": self.default_main_model,
            "subagent_model": self.default_subagent_model,
            "design_model": self.default_design_model,
        }

    async def list_saved_sessions(self) -> List[Dict[str, Any]]:
        """
        List all saved sessions from disk.

        Returns:
            List of session metadata
        """
        return self.session_persistence.list_sessions()

    async def load_saved_session(self, session_id: str) -> bool:
        """
        Load a saved session from disk.

        Args:
            session_id: Session identifier

        Returns:
            True if successful
        """
        success = self.session_manager.load_from_disk(session_id)
        if success:
            logger.info(f"Loaded session {session_id} from disk")
        else:
            logger.warning(f"Failed to load session {session_id} from disk")
        return success

    async def delete_saved_session(self, session_id: str) -> bool:
        """
        Delete a session from disk and memory.

        Args:
            session_id: Session identifier

        Returns:
            True if successful
        """
        return self.session_manager.delete_from_disk(session_id)

    async def export_session(self, session_id: str, output_path: str) -> Tuple[bool, Optional[str]]:
        """
        Export session to a zip file.

        Args:
            session_id: Session identifier
            output_path: Target path for zip file

        Returns:
            Tuple of (success, error_message)
        """
        return self.session_persistence.export_session(session_id, output_path)

    async def import_session(self, archive_path: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Import session from a zip archive.

        Args:
            archive_path: Path to zip file

        Returns:
            Tuple of (success, session_id, error_message)
        """
        return self.session_persistence.import_session(archive_path)

    def set_clarification_callback(self, callback: callable):
        """
        Set custom clarification callback.

        Args:
            callback: Function that takes (question, intention) and returns response
        """
        self._clarification_callback = callback
        set_user_clarification_callback(callback)

    async def get_a2a_agents(self) -> Dict[str, Dict[str, Any]]:
        """
        Get registered A2A agents with their status.

        Returns:
            Dict mapping agent names to their info (url, online status, description, capabilities)
        """
        logger.info(f"get_a2a_agents called. Middleware has {len(self.a2a_middleware.remote_agents)} agents")
        agents_info = {}
        
        for agent_name, client in self.a2a_middleware.remote_agents.items():
            logger.info(f"Processing agent: {agent_name}, has agent_card: {client.agent_card is not None}")
            agent_info = {
                'online': client.agent_card is not None,
                'url': client.base_url,
            }
            
            if client.agent_card:
                agent_info['description'] = client.agent_card.get('description', 'N/A')
                agent_info['capabilities'] = client.agent_card.get('capabilities', [])
                agent_info['version'] = client.agent_card.get('version', 'N/A')
                logger.info(f"  Agent {agent_name} is ONLINE")
            else:
                logger.info(f"  Agent {agent_name} is OFFLINE (no agent_card)")
            
            agents_info[agent_name] = agent_info
        
        logger.info(f"Returning {len(agents_info)} agents")
        return agents_info

    async def refresh_a2a_agents(self) -> Dict[str, Dict[str, Any]]:
        """
        Re-discover A2A agents from registry.

        Returns:
            Updated dict of agent info
        """
        logger.info("Refreshing A2A agents...")
        
        # Find registry path
        try:
            import a2a_capability
            registry_path = Path(a2a_capability.__file__).parent / "agents_registry.json"
        except ImportError:
            registry_path = Path.cwd() / "a2a_capability" / "agents_registry.json"
        
        if registry_path.exists():
            # Recreate middleware (don't cleanup old one to avoid event loop issues)
            self.a2a_middleware = A2AHTTPMiddleware()
            
            # Re-register
            try:
                await self.a2a_middleware.register_agents_from_file(str(registry_path))
                self.a2a_tools = self.a2a_middleware.tools
                logger.info(f"✅ Refreshed {len(self.a2a_tools)} A2A tools")
                logger.info(f"✅ Agents: {list(self.a2a_middleware.remote_agents.keys())}")
            except Exception as e:
                logger.error(f"❌ Failed to refresh A2A agents: {e}", exc_info=True)
                self.a2a_tools = []
        else:
            logger.warning(f"⚠️ A2A registry not found at {registry_path}")
        
        return await self.get_a2a_agents()
