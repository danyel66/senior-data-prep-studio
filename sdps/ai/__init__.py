"""AI 'Senior Principal Data Architect' challenge generator (Claude or Ollama). Qt-free."""
from .challenge import CATEGORIES, CHALLENGE_SCHEMA, DIFFICULTIES, TOPICS, Challenge, ChallengeFormatError, \
    ChallengeStore
from .config import CLAUDE_MODELS, AIConfig, claude_credentials_hint, get_api_key, keyring_available, set_api_key
from .generator import ChallengeGenerator, GenerationResult
from .providers import AICancelled, AIError, ClaudeProvider, Completion, OllamaProvider, Provider, create_provider

__all__ = ["AICancelled", "AIConfig", "AIError", "CATEGORIES", "CHALLENGE_SCHEMA", "CLAUDE_MODELS", "Challenge",
           "ChallengeFormatError", "ChallengeGenerator", "ChallengeStore", "ClaudeProvider", "Completion",
           "DIFFICULTIES", "GenerationResult", "OllamaProvider", "Provider", "TOPICS", "claude_credentials_hint",
           "create_provider", "get_api_key", "keyring_available", "set_api_key"]
