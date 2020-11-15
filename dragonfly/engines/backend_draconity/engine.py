from dragonfly.engines.backend_draconity.dictation import DraconityDictationContainer
from dragonfly.grammar.grammar_base import Grammar
from dragonfly.engines.base.recobs import RecObsManagerBase
from dragonfly.engines.base.engine import EngineError
import sys
from typing import Dict

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue

from ..base import EngineBase
from ..backend_natlink.compiler import NatlinkCompiler
from ...grammar import state as state_
from .config import _DraconityConfig
from .inject import inject_draconity
from .stream import TCPStream
from .client import DraconityClient, prep_auth, prep_grammar_set, prep_grammar_unload, prep_status, prep_mimic, prep_unpause
from dragonfly import Window

def format_message(msg):
    # Remove binary blobs from a message so it can be printed
    msg_str = msg.copy()
    if "cmd" in msg_str and isinstance(msg_str["cmd"], dict) and msg_str["cmd"]["cmd"] == "g.set":
        msg_str["cmd"]["data"] = "..."
        msg_str["cmd"]["lists"] = "..."
    if "cmd" in msg_str and msg_str["cmd"] == "p.end":
        msg_str["wav"] = "..."
    return msg_str

class _FunctionLoop(object):
    """Message loop that allows functions to be queued."""

    def __init__(self):
        self._queue = Queue()

    def queue_function(self, func, *args, **kwargs):
        """Push a function onto the queue."""
        if not callable(func):
            raise ValueError("Func must be callable, was: {}".format(type(func)))
        self._queue.put(lambda: func(*args, **kwargs))

    def pump_messages(self):
        """Repeatedly execute queued functions until one raises `Finished`.

        """
        try:
            while True:
                self._pump_message(self._queue)
        except _FunctionLoop.Finished:
            pass

    @staticmethod
    def _pump_message(queue):
        """Pop a function (wait until one is available), then execute it."""
        func = queue.get()
        return func()

    class Finished(Exception):
        """Raise this to break out of a message loop."""


class DraconityEngine(EngineBase):
    """Draconity-based engine backend."""

    _name = "draconity"
    DictationContainer = DraconityDictationContainer

    def __init__(self, injector_path, draconity_path, dragon_old_version=False):
        super(DraconityEngine, self).__init__()

        # Path to inject.exe
        self.injector_path = injector_path
        # Path to libdraconity.dll
        self.draconity_path = draconity_path

        # Because dragon added a new member to the dsx_word_node struct
        # in version 15 the rule id (which we need to decode the recognition)
        # can be in two places depending on version.
        #
        # Draconity now sends both, the user tells us which version they are running
        # and we decide on our end which to use.
        self.rule_id_key = "rule" if not dragon_old_version else "old_rule"

        self._language = "en"

        self._recognition_observer_manager = RecObsManagerBase(self)
        self._message_loop = _FunctionLoop()

        # TODO: Create if doesn't exist
        self.config = _DraconityConfig.load_from_disk()
        self.config.assert_valid_connection()

    def connect(self):
        try:
            stream = TCPStream(self.config.tcp_host, self.config.tcp_port)
            self._log.info("Connected to existing draconity instance.")
        except ConnectionRefusedError:
            self._log.info("Injecting draconity.")
            inject_draconity(self.injector_path, self.draconity_path)
            stream = TCPStream(self.config.tcp_host, self.config.tcp_port)

        self.client = DraconityClient(self.handle_message)

        self.client.connect(stream)
        self._log.info("Client successfully connected")

        self.queue_send(prep_auth(self.config.secret))
        self.queue_send(prep_status())

    def disconnect(self):
        # TODO: Unload grammars? I think draconity does it anyway
        self.client.close()

    def load_grammar(self, grammar):
        if grammar.name in self._grammar_wrappers:
            self._log.warning("Grammar %s loaded multiple times." % grammar)
            return

        wrapper = self._load_grammar(grammar)
        self._grammar_wrappers[grammar.name] = wrapper

    def _load_grammar(self, grammar):
        self._log.debug("Engine %s: loading grammar %s.", self, grammar.name)

        c = NatlinkCompiler()
        (compiled_grammar, grammar._rule_names) = c.compile_grammar(grammar)

        state = prep_grammar_set(grammar.name, compiled_grammar)
        return GrammarWrapper(grammar, state, self)

    def unload_grammar(self, grammar):
        wrapper = self._grammar_wrappers.pop(grammar.name)
        if not wrapper:
            raise EngineError("Grammar %s cannot be unloaded because"
                              " it was not loaded.", grammar)
        self._unload_grammar(grammar, wrapper)

    def _unload_grammar(self, grammar, wrapper):
        self._log.debug("Engine %s: unloading grammar %s.", self, grammar.name)
        msg = prep_grammar_unload(grammar.name)
        self.queue_send(msg)

    def _get_grammar_wrapper(self, grammar):
        grammar_name = grammar.name if isinstance(grammar, Grammar) else grammar
        if grammar_name not in self._grammar_wrappers:
            return None
        wrapper = self._grammar_wrappers[grammar_name]
        return wrapper

    # We don't actually need to do anything here,
    # grammar activation is controlled by activating rules
    def activate_grammar(self, grammar):
        self._log.debug("Activating grammar %s.", grammar.name)
        pass

    def deactivate_grammar(self, grammar):
        self._log.debug("Deactivating grammar %s.", grammar.name)
        pass

    def update_all_grammars(self):
        fg_window = Window.get_foreground()

        for wrapper in self._iter_all_grammar_wrappers_dynamically():
            wrapper.grammar.process_begin(
                fg_window.executable,
                fg_window.title,
                fg_window.handle
            )

        for wrapper in self._iter_all_grammar_wrappers_dynamically():
            state, changed = wrapper.update_state()
            if changed:
                self.queue_send(state)

    #------------------------------------------------

    # The rule/list will change its own activity status/elements
    # then we flush at the end of update_all_grammars
    def activate_rule(self, rule, grammar):
        self._log.debug("Activating rule %s.", rule.name)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot activate rule %s for grammar %s, as the grammar is not loaded.", rule.name, grammar)
            return
        wrapper.dirty = True

    def deactivate_rule(self, rule, grammar):
        # TODO: Get rid of %'s
        self._log.debug("Dectivating rule %s.", rule.name)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot deactivate rule %s for grammar %s, as the grammar is not loaded.", rule.name, grammar)
            return
        wrapper.dirty = True

    def update_list(self, lst, grammar):
        self._log.debug("Updating list %s in grammar %s", lst, grammar)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot update list %s in grammar %s, as the grammar is not loaded.", lst, grammar)
            return
        wrapper.dirty = True

    def set_exclusiveness(self, grammar, exclusive):
        self._log.debug("Setting exclusiveness of grammar %s to %s.", grammar, exclusive)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot set exclusiveness of grammar %s, as the grammar is not loaded.", grammar)
            return
        if wrapper.exclusive != exclusive:
            wrapper.set_exclusiveness(exclusive)

    #------------------------------------------------

    def mimic(self, words):
        """Mimic a recognition of the given `words`.

        :param list words: list of words to mimic.

        """
        self._log.debug("Mimicking words %r", words)
        msg = prep_mimic(words)
        self.queue_send(msg)

    def _do_recognition(self):
        try:
            self._message_loop.pump_messages()
        except:
            self.disconnect()

    def queue_send(self, msg):
        self._message_loop.queue_function(self.client.send, msg)

    def handle_message(self, tid: int, msg: Dict):
        try:
            self._log.debug("[%i] %s", tid, format_message(msg))
            # Parse message and dispatch
            if "topic" in msg:
                topic = msg["topic"]
                if topic == "paused":
                    self.update_all_grammars()
                    self.queue_send(prep_unpause(msg["token"]))
                elif topic == "phrase":
                    cmd = msg["cmd"]
                    if cmd == "p.begin":
                        self._recognition_observer_manager.notify_begin()
                    elif cmd == "p.end":
                        if msg["phrase"]:
                            self.phrase_end(msg)
                elif topic == "g.set" and not msg["success"]:
                    # g.set failed, try again
                    wrapper = self._get_grammar_wrapper(msg["name"])
                    if not wrapper:
                        # Not sure how this would happen, hopefully it won't
                        self._log.warning("Received a failure notification in setting grammar %s, but could not find it in _grammar_wrappers.", msg["name"])
                    else:
                        wrapper.dirty = True
            if "language_id" in msg:
                self._set_language(msg["language_id"])

            if "success" in msg and not msg["success"]:
                # Something went wrong
                self._recognition_observer_manager.notify_failure()
                self._log.error("Error received from draconity: %s", format_message(msg))
        except:
            self._log.error("Error handling message: %s", format_message(msg), exc_info=True)

    def phrase_end(self, data):
        grammar_name = data["grammar"]
        wrapper = self._get_grammar_wrapper(grammar_name)

        words = data["phrase"]

        # Call the grammar"s general process_recognition method, if present.
        func = getattr(wrapper.grammar, "process_recognition", None)
        if func:
            if not func(words):
                return

        rule_ids = [d[self.rule_id_key] for d in data["words"]]
        wrapper.results_callback(words, rule_ids)

    def _get_language(self):
        return self._language

    def _set_language(self, language_code):
        if language_code in self._language_ids:
            language = self._language_ids[language_code]
            self._log.debug("Setting language to '%s'", language)
            self._language = language
            return

        # Speaker language wasn't found.
        self._log.error("Unknown speaker language in draconity status packet: 0x%04x", language_code)

    _language_ids = {
        # TODO: Investigate these
        1: "en",
    }


class GrammarWrapper(object):

    def __init__(self, grammar, state, engine):
        self.grammar = grammar
        # Keep track of last "g.set" request sent, when we need to update the grammar
        # we update this and send it again
        self.state = state
        self.engine = engine
        self.exclusive = False
        # Grammar won't be loaded until the first pause
        self.dirty = True

    def up_to_date(self):
        # Flags keep track of what has changed since the last update
        self.dirty = False

    def set_exclusiveness(self, exclusive):
        self.dirty = True
        self.exclusive = exclusive

    def update_state(self):
        # TODO: Finish this comment...
        #
        # There are three things which might change during grammar.process_begin:
        # - Rules can be activated and deactivated
        # - Dynamic lists can be updated
        # - Exclusiveness can be changed
        #
        # Draconity uses the g.set call for all of these: we just set the grammar to the way we want it to be. ...
        #
        # Line 445 in grammar_base:
        # [r.deactivate() for r in self._rules if r.active]
        # would cause spam
        #

        changed = self.dirty
        if self.dirty:
            self.state.update({
                "active_rules": [r.name for r in self.grammar._rules if r.active and r.exported],
                "lists": {
                    lst.name: lst.get_list_items()
                    for lst in self.grammar.lists
                },
                "exclusive": self.exclusive,
            })
        self.up_to_date()
        return self.state, changed


    def results_callback(self, words, rule_ids):
        DraconityEngine._log.debug("Grammar %s: received recognition %r.", self.grammar.name, words)

        words_rules = list(zip(words, rule_ids))
        s = state_.State(words_rules, self.grammar._rule_names, self.engine)
        for r in self.grammar._rules:
            if not (r.active and r.exported): continue
            s.initialize_decoding()
            for _ in r.decode(s):
                if s.finished():
                    self.engine._recognition_observer_manager.notify_recognition(words)
                    root = s.build_parse_tree()
                    r.process_recognition(root)
                    return

        DraconityEngine._log.warning("Grammar %s: failed to decode"
                                   " recognition %r.", self.grammar.name, words_rules)

