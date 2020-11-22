import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

from six import text_type, binary_type, string_types, PY2

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue

from ..base import EngineBase, EngineError, RecObsManagerBase, GrammarWrapperBase
from ..backend_natlink.compiler import NatlinkCompiler
from ...grammar import state as state_
from ...grammar.grammar_base import Grammar
from .config import _DraconityConfig
from .stream import TCPStream
from .client import DraconityClient, prep_auth, prep_grammar_set, prep_grammar_unload, prep_status, prep_mimic, prep_unpause
from .dictation import DraconityDictationContainer
from ...windows import Window

def format_message(msg):
    # Remove binary blobs from a message so it can be printed
    msg_str = msg.copy()
    if "cmd" in msg_str and isinstance(msg_str["cmd"], dict) and msg_str["cmd"]["cmd"] == "g.set":
        msg_str["cmd"]["data"] = "..."
        msg_str["cmd"]["lists"] = "..."
    if "wav" in msg_str:
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

    def __init__(self,
                 injector_path,
                 draconity_path,
                 dragon_old_version=False,
                 retain_dir=None):
        super(DraconityEngine, self).__init__()

        # Path to inject.exe
        injector_path = Path(injector_path)
        self.injector_path = str(injector_path.resolve(strict=True))

        # Path to libdraconity.dll
        draconity_path = Path(draconity_path)
        self.draconity_path = str(draconity_path.resolve(strict=True))

        # Because dragon added a new member to the dsx_word_node struct
        # in version 15 the rule id (which we need to decode the recognition)
        # can be in two places depending on version.
        #
        # Draconity now sends both, the user tells us which version they are running
        # and we decide on our end which to use.
        self.rule_id_key = "rule" if not dragon_old_version else "old_rule"

        if retain_dir and not os.path.isdir(retain_dir):
            self._log.warning(
                "Audio will not be retained because '%s' is not a "
                "directory.", retain_dir
            )
            self._retain_dir = None
        else:
            self._retain_dir = retain_dir

        self._language = "en"

        self._recognition_observer_manager = RecObsManagerBase(self)
        self._message_loop = _FunctionLoop()

        self._config = _DraconityConfig.load_from_disk()
        self._config.assert_valid_connection()

    def inject_draconity(self):
        # TODO: Make this work on older python versions
        self._log.info("Injecting draconity.")
        result = subprocess.run([
            self.injector_path,
            "natspeak.exe",
            self.draconity_path])
        if result.returncode:
            raise EngineError("Failed to inject '%s'. Output from injector was:\n%s", self.draconity_path, result.stdout)
        self._log.info("Injection successful.")

    def connect(self):
        try:
            stream = TCPStream(self._config.tcp_host, self._config.tcp_port)
            self._log.info("Connected to existing draconity instance.")
        except ConnectionRefusedError:
            self.inject_draconity()
            stream = TCPStream(self._config.tcp_host, self._config.tcp_port)

        self.client = DraconityClient(self.handle_message, self.handle_error, self.handle_disconnect)
        self.client.connect(stream)
        self._log.info("Client successfully connected.")

        self.queue_send(prep_auth(self._config.secret))
        self.queue_send(prep_status())

    def disconnect(self):
        for wrapper in self._iter_all_grammar_wrappers_dynamically():
            self.unload_grammar(wrapper.grammar)
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
        return GrammarWrapper(grammar, state, self, self._recognition_observer_manager)

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
        self._log.debug("Activating rule %s in grammar %s.", rule.name, grammar.name)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot activate rule %s for grammar %s, as the grammar is not loaded.", rule.name, grammar)
            return
        wrapper.dirty = True

    def deactivate_rule(self, rule, grammar):
        self._log.debug("Dectivating rule %s in grammar %s.", rule.name, grammar.name)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot deactivate rule %s for grammar %s, as the grammar is not loaded.", rule.name, grammar.name)
            return
        wrapper.dirty = True

    def update_list(self, lst, grammar):
        self._log.debug("Updating list %s in grammar %s", lst, grammar.name)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot update list %s in grammar %s, as the grammar is not loaded.", lst, grammar.name)
            return
        wrapper.dirty = True

    def set_exclusiveness(self, grammar, exclusive):
        self._log.debug("Setting exclusiveness of grammar %s to %s.", grammar.name, exclusive)
        wrapper = self._get_grammar_wrapper(grammar)
        if not wrapper:
            self._log.warning("Cannot set exclusiveness of grammar %s, as the grammar is not loaded.", grammar.name)
            return
        if wrapper.exclusive != exclusive:
            wrapper.exclusive = exclusive
            wrapper.dirty = True

    def mimic(self, words):
        """Mimic a recognition of the given `words`.

        :param list words: list of words to mimic.

        """
        self._log.debug("Mimicking words %r", words)
        msg = prep_mimic(words)
        self.queue_send(msg)

    #------------------------------------------------

    def _do_recognition(self):
        self._message_loop.pump_messages()
        self.disconnect()

    def queue_send(self, msg):
        self._message_loop.queue_function(self.client.send, msg)

    def handle_error(self, error):
        self._log.error(error, exc_info=True)

    def handle_disconnect(self):
        self._message_loop.queue_function(self.finished)

    def finished(self):
        raise _FunctionLoop.Finished

    def handle_message(self, tid, msg):
        self._message_loop.queue_function(self._handle_message, tid, msg)

    def _handle_message(self, tid, msg):
        # TODO: Should probably do this in a more disciplined way.
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
                    self._log.warning("Received a failure message when setting a grammar, %s", format_message(msg))
                    wrapper = self._get_grammar_wrapper(msg["name"])
                    if wrapper:
                        wrapper.dirty = True
                    else:
                        self._log.warning("Received a failure notification in setting grammar %s, but could not find it in _grammar_wrappers.", msg["name"])
            if "language_id" in msg:
                if msg["language_id"] < 0:
                    # Engine not ready yet
                    self.queue_send(prep_status())
                else:
                    self._set_language(msg["language_id"])

            if "success" in msg and not msg["success"]:
                # Something went wrong
                self._recognition_observer_manager.notify_failure(msg)
                self._log.error("Error received from draconity: %s", format_message(msg))
        except:
            self._log.error("Error handling message: %s", format_message(msg), exc_info=True)

    def phrase_end(self, results):
        # TODO: Investigate
        # https://github.com/dictation-toolbox/dragonfly/commit/9dbf1ce6b95d6aee63e0275dd66a0df6e9a751db
        # If we want to include this fix then can just make
        # GrammarWrapper a subclass of NatlinkGrammarWrapper and
        # call the _process_rules method on it from here.

        grammar_name = results["grammar"]
        wrapper = self._get_grammar_wrapper(grammar_name)
        grammar = wrapper.grammar

        words = results["phrase"]
        rule_ids = [d[self.rule_id_key] for d in results["words"]]
        assert len(words) == len(rule_ids)
        words_rules = list(zip(words, rule_ids))

        self._log.info("Grammar %s: received recognition %r.", grammar.name, words_rules)

        # Call the grammar"s general process_recognition method, if present.
        func = getattr(wrapper.grammar, "process_recognition", None)
        if func:
            if not wrapper._process_grammar_callback(func, words=words,
                                                  results=results):
                # Return early if the method didn't return True or equiv.
                return

        s = state_.State(words_rules, grammar._rule_names, self)
        for r in grammar._rules:
            if not (r.active and r.exported): continue
            s.initialize_decoding()
            for _ in r.decode(s):
                if s.finished():
                    self._retain_audio(words, results, r.name, grammar)
                    root = s.build_parse_tree()
                    self._recognition_observer_manager.notify_recognition(words, r, root, results)
                    r.process_recognition(root)
                    self._recognition_observer_manager.notify_post_recognition(words, r, root, results)
                    return

        self._log.warning("Grammar %s: failed to decode"
                                   " recognition %r.", grammar.name, words_rules)


    def _retain_audio(self, words, results, rule_name, grammar):
        if not self._retain_dir:
            return
        retain_dir = self._retain_dir
        if "wav" in results:
            audio = results["wav"]
            if len(audio) > 0:
                now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
                filename = "retain_%s.wav" % now
                wav_path = os.path.join(retain_dir, filename)
                with open(wav_path, "wb") as f:
                    f.write(audio)

                # Write metadata, assuming 11025Hz 16bit mono audio
                text = ' '.join(words)
                audio_length = float(len(audio) / 2) / 11025
                tsv_path = os.path.join(retain_dir, "retain.tsv")
                with open(tsv_path, "a") as tsv_file:
                    tsv_file.write('\t'.join([
                        filename, text_type(audio_length),
                        grammar.name, rule_name, text
                    ]) + '\n')
        else:
            self._log.warning("Failed to retain audio as the 'p.end' message did not contain audio data. The message was: %s", format_message(results))


    def _has_quoted_words_support(self):
        return True

    def _get_language(self):
        return self._language

    def _set_language(self, language_code):
        if language_code in self._language_ids:
            language = self._language_ids[language_code]
            if self._language != language:
                self._log.debug("Setting language to '%s'", language)
                self._language = language
            return

        # Speaker language wasn't found.
        self._log.error("Unknown speaker language in draconity status packet: 0x%04x", language_code)

    _language_ids = {
        # TODO: Investigate these
        1: "en",
    }


class GrammarWrapper(GrammarWrapperBase):

    def __init__(self, grammar, state, engine, recobs_manager):
        GrammarWrapperBase.__init__(self, grammar, engine, recobs_manager)
        self.grammar = grammar
        # Keep track of last "g.set" request sent, when we need to update the grammar
        # we update this and send it again
        self.state = state
        self.engine = engine
        self.exclusive = False
        # Grammar won't be loaded until the first pause
        self.dirty = True

    def update_state(self):
        #
        # There are three things which might change during grammar.process_begin:
        # - Rules can be activated and deactivated
        # - Dynamic lists can be updated
        # - Exclusiveness can be changed
        #
        # Draconity uses the g.set call for all of these: we just set the grammar to the way we want it to be.
        #
        # To avoid spamming these calls with stuff like [r.deactivate() for r in self._rules if r.active]
        # the engine methods for these things don't actually call g.set, they just set
        # dirty = True on the grammar's wrapper, indicating that the new state needs to be flushed.
        #
        # Once all process_begin calls have finished and all grammars have updated themselves
        # we recompute the active rules, lists and exclusiveness for all dirty grammars
        # and send a single g.set message with the new state.
        #
        changed = False
        if self.dirty:
            changed = True
            self.state.update({
                "active_rules": [r.name for r in self.grammar._rules if r.active and r.exported],
                "lists": {
                    lst.name: lst.get_list_items()
                    for lst in self.grammar.lists
                },
                "exclusive": self.exclusive,
            })
        self.dirty = False
        return self.state, changed

    # TODO: _retain_audio, should be simple
