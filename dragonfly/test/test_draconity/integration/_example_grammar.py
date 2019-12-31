# -*- mode:fundamental -*-
# ^ Long lines kill Emacs, this helps it cope.

"""This module contains a working, compiled Dragon grammar.

This grammar can be used to test grammar loading without needing to compile one
from scratch. It will be identical on both Python 2 & 3.

The grammar is defined as follows:

```
    class ExampleCustomRule(CompoundRule):

        spec = "I want to eat <food>"
        extras = [
            Choice(
                "food", {"(an | a juicy) apple": "good", "a [greasy] hamburger": "bad"}
            )
        ]

        def _process_recognition(self, node, extras):
            good_or_bad = extras["food"]
            print("That is a %s idea!" % good_or_bad)


    class AnotherCustomRule(CompoundRule):

        spec = "I like to drink beer"

        def _process_recognition(self, node, extras):
            print("I can't serve you. You're drunk!")


    grammar = Grammar(name="example_grammar")
    example_rule = ExampleCustomRule()
    another_rule = AnotherCustomRule()
    grammar.add_rule(example_rule)
    grammar.add_rule(another_rule)

    ...

```

To test each rule, these two phrases should work:

    `example_rule`: "I want to eat an apple

    `another_rule`: "I like to drink beer"

"""

import codecs
import bson

name = "example_grammar"
blob_hex = "000000000000000004000000380000001c000000010000004578616d706c65437573746f6d52756c650000001c00000002000000416e6f74686572437573746f6d52756c650000000500000000000000060000000000000002000000c00000000c0000000100000049000000100000000200000077616e74000000000c00000003000000746f00000c00000004000000656174000c000000050000006100000010000000060000006772656173790000140000000700000068616d6275726765720000000c00000008000000616e000010000000090000006a75696379000000100000000a0000006170706c65000000100000000b0000006c696b6500000000100000000c0000006472696e6b000000100000000d00000062656572000000000300000020010000e00000000100000001000000010000000100000001000000030000000100000003000000020000000300000003000000030000000400000002000000010000000100000002000000010000000100000003000000050000000100000004000000030000000600000002000000040000000300000007000000020000000100000001000000010000000100000002000000030000000800000001000000010000000300000005000000030000000900000002000000010000000200000002000000030000000a000000020000000100000002000000020000000200000001000000400000000200000001000000010000000300000001000000030000000b0000000300000003000000030000000c000000030000000d0000000200000001000000"
_decode_hex = codecs.getdecoder("hex_codec")
blob_binary = _decode_hex(blob_hex)[0]
active_rules = ["ExampleCustomRule", "AnotherCustomRule"]
# TODO: Replace this with an example grammar that does use lists.
lists = {}
