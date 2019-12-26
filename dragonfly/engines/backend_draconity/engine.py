from ..base import EngineBase


class DraconityEngine(EngineBase):
    """Draconity-based engine backend."""

    _name = "draconity"

    def __init__(self):
        super(DraconityEngine, self).__init__()

    def connect(self):
        raise NotImplementedError("Not yet implemented.")

    def disconnect(self):
        raise NotImplementedError("Not yet implemented.")

    def _load_grammar(self, grammar):
        raise NotImplementedError("Not yet implemented.")

    def _unload_grammar(self, grammar):
        raise NotImplementedError("Not yet implemented.")

    def update_list(self, lst, grammar):
        raise NotImplementedError("Not yet implemented.")

    # TODO: Do we need these? Can we just discard them?
    def activate_grammar(self, grammar):
        # Rules are managed individually - no activation at the grammar level.
        pass

    def deactivate_grammar(self, grammar):
        # Rules are managed individually - no deactivation at the grammar
        # level.
        pass

    def activate_rule(self, rule, grammar):
        raise NotImplementedError("Not yet implemented.")

    def deactivate_rule(self, rule, grammar):
        raise NotImplementedError("Not yet implemented.")

    def set_exclusiveness(self, grammar, exclusive):
        raise NotImplementedError("Not yet implemented.")

    def mimic(self, words):
        """Mimic a recognition of the given `words`.

        :param list words: list of words to mimic.

        """
        raise NotImplementedError("Not yet implemented.")

    def speak(self, text):
        """Speak the given `text` using text-to-speech."""
        # TODO: Defer to a default TTS interface?
        raise NotImplementedError("Draconity does not support text-to-speech.")

    # TODO: Language features? `_get_language`?
