"""Ordered delivery to registered event observers.

Created: 2026-10-29T14:20:28+00:00
Author: Michel Bierlaire, EPFL
"""

from multiobjective_vns.events import AlgorithmEvent, EventObserver
from multiobjective_vns.identifiers import ComponentId, identifier_text
from multiobjective_vns.records.failures import ComponentFailure, FailureKind


class ObserverDispatcher:
    """Deliver each appended event once and deactivate failing observers.

    :param observers: Event observers in registration order.
    :raises TypeError: If an observer does not provide the event callback.
    :raises ValueError: If observer component identifiers are duplicated.
    """

    __slots__ = ('_active_observers', '_next_sequence')

    def __init__(self, observers: tuple[EventObserver, ...] = ()) -> None:
        if not isinstance(observers, tuple) or any(
            not callable(getattr(observer, 'on_event', None)) for observer in observers
        ):
            raise TypeError('observers must be a tuple of EventObserver values')
        registrations = tuple(
            (observer, observer.component_id) for observer in observers
        )
        observer_texts = tuple(
            identifier_text(component_id) for _, component_id in registrations
        )
        if len(observer_texts) != len(set(observer_texts)):
            raise ValueError('observer component identifiers must be unique')
        self._active_observers: list[tuple[EventObserver, ComponentId]] = list(
            registrations
        )
        self._next_sequence = 0

    def deliver(
        self, events: tuple[AlgorithmEvent, ...]
    ) -> tuple[ComponentFailure, ...]:
        """Deliver one contiguous batch in sequence order.

        :param events: Event snapshots already appended to the invocation log.
        :returns: Failures raised by observers during this batch.
        :raises TypeError: If the batch contains malformed event values.
        :raises ValueError: If event sequence numbers are not contiguous.
        """
        if not isinstance(events, tuple) or not events:
            raise ValueError('events must be a nonempty tuple')
        for offset, event in enumerate(events):
            if not isinstance(event, AlgorithmEvent):
                raise TypeError('events must contain AlgorithmEvent values')
            if event.sequence_number != self._next_sequence + offset:
                raise ValueError('events must arrive in contiguous sequence order')

        failures: list[ComponentFailure] = []
        for event in events:
            for observer, component_id in tuple(self._active_observers):
                try:
                    observer.on_event(event)
                except Exception as error:
                    self._active_observers = [
                        (active, active_id)
                        for active, active_id in self._active_observers
                        if active is not observer
                    ]
                    failures.append(
                        ComponentFailure(
                            FailureKind.CALLBACK_EXCEPTION,
                            component_id,
                            'event_delivery',
                            f'EventObserver.on_event raised: {error}',
                            solution_id=event.solution_id,
                            choice_id=event.choice_id,
                        )
                    )
        self._next_sequence += len(events)
        return tuple(failures)


__all__ = ['ObserverDispatcher']
