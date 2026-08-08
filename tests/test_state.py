from __future__ import annotations

from flowtext.state import State, StateMachine


def test_starts_idle() -> None:
    assert StateMachine().current is State.IDLE


def test_happy_path() -> None:
    sm = StateMachine()
    assert sm.transition(State.RECORDING)
    assert sm.transition(State.PROCESSING)
    assert sm.transition(State.IDLE)


def test_illegal_transition_is_rejected() -> None:
    """Single-Flight: aus PROCESSING darf nicht direkt aufgenommen werden."""
    sm = StateMachine()
    sm.transition(State.RECORDING)
    sm.transition(State.PROCESSING)
    assert sm.transition(State.RECORDING) is False
    assert sm.current is State.PROCESSING


def test_same_state_is_noop() -> None:
    sm = StateMachine()
    seen: list[tuple[State, State]] = []
    sm.subscribe(lambda a, b: seen.append((a, b)))
    assert sm.transition(State.IDLE)
    assert seen == []


def test_observers_are_notified() -> None:
    sm = StateMachine()
    seen: list[tuple[State, State]] = []
    sm.subscribe(lambda a, b: seen.append((a, b)))
    sm.transition(State.RECORDING)
    assert seen == [(State.IDLE, State.RECORDING)]


def test_broken_observer_does_not_break_transition() -> None:
    sm = StateMachine()
    calls: list[str] = []

    def boom(previous: State, current: State) -> None:
        raise RuntimeError("kaputt")

    sm.subscribe(boom)
    sm.subscribe(lambda a, b: calls.append("ok"))
    assert sm.transition(State.RECORDING)
    assert sm.current is State.RECORDING
    assert calls == ["ok"]


def test_error_recovers_to_idle() -> None:
    sm = StateMachine()
    sm.transition(State.RECORDING)
    assert sm.transition(State.ERROR)
    assert sm.transition(State.IDLE)
    assert sm.current is State.IDLE
