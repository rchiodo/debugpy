# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See LICENSE in the project root
# for license information.

import pytest

from tests import debug
from tests.debug import targets
from tests.patterns import some


@pytest.mark.parametrize("jmc", ["jmc", ""])
@pytest.mark.parametrize("target", targets.all_named)
def test_justmycode_frames(pyfile, target, run, jmc):
    @pyfile
    def code_to_debug():
        import debuggee

        debuggee.setup()

        import this  # @bp

        assert this

    with debug.Session() as session:
        session.config["justMyCode"] = bool(jmc)

        with run(session, target(code_to_debug)):
            session.set_breakpoints(code_to_debug, all)

        stop = session.wait_for_stop(
            "breakpoint", expected_frames=[some.dap.frame(code_to_debug, "bp")]
        )
        if jmc:
            assert len(stop.frames) == 1
        else:
            assert len(stop.frames) >= 1

        session.request("stepIn", {"threadId": stop.thread_id})

        # With JMC, it should step out of the function, remaining in the same file.
        # Without JMC, it should step into stdlib.
        expected_path = some.path(code_to_debug)
        if not jmc:
            expected_path = ~expected_path
        session.wait_for_stop(
            "step",
            expected_frames=[some.dap.frame(some.dap.source(expected_path), some.int)],
        )

        session.request_continue()


@pytest.mark.parametrize("target", targets.all_named)
def test_step_into_library_when_justmycode_is_false_via_config(pyfile, target, run):
    """
    Tests that when justMyCode is set to False in the session config
    (which, due to recent changes, should translate to --configure-justMyCode False
    for the server), the debugger correctly steps into library code (e.g., during an import).
    """
    @pyfile
    def code_to_debug():
        import debuggee
        debuggee.setup()
        import this  # @bp
        assert this

    with debug.Session() as session:
        session.config["justMyCode"] = False # Explicitly disable JMC

        with run(session, target(code_to_debug)):
            session.set_breakpoints(code_to_debug, all)

        stop = session.wait_for_stop(
            "breakpoint", expected_frames=[some.dap.frame(code_to_debug, "bp")]
        )
        
        # When JMC is off, we expect to see frames from stdlib import machinery
        # or the debugger itself. The key is that it's not strictly 1.
        assert len(stop.frames) >= 1 

        session.request("stepIn", {"threadId": stop.thread_id})

        # Without JMC, it should step into stdlib (the 'this' module's execution or import process).
        # So, the path of the current frame should NOT be code_to_debug.py.
        session.wait_for_stop(
            "step",
            expected_frames=[some.dap.frame(some.dap.source(path=~some.path(code_to_debug)), some.int)],
        )

        session.request_continue()
