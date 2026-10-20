"""AI categorization: provenance events, memory and the classifier."""
# Imported for its side effect: registers the memory learner as a user-choice
# hook, so it is wired wherever `events` is used.
from app.services.categorization import memory as _memory  # noqa: F401
