"""A long chat is read a window at a time: the latest messages, then older ones before a turn."""
from orion.api.services.job_service import history_window


def chat(turns):
    history = []
    for turn in range(turns):
        history += [{'role': 'user', 'turn_id': f't{turn}', 'content': f'q{turn}'},
                    {'role': 'assistant', 'turn_id': f't{turn}', 'content': f'a{turn}'}]
    return history


def test_without_a_window_everything_is_returned():
    assert history_window(chat(3)) == chat(3)


def test_the_latest_window_has_one_extra_message_when_older_ones_exist():
    window = history_window(chat(10), limit=4)
    assert [m['content'] for m in window] == ['a7', 'q8', 'a8', 'q9', 'a9']
    assert history_window(chat(2), limit=4) == chat(2)  # Nothing older: no extra message.


def test_older_messages_end_before_the_first_message_of_a_turn():
    window = history_window(chat(10), limit=4, before_turn='t7')
    assert [m['content'] for m in window] == ['a4', 'q5', 'a5', 'q6', 'a6']
    assert history_window(chat(10), limit=4, before_turn='t1') == chat(1)
    assert history_window(chat(10), limit=4, before_turn='t0') == []


def test_an_unknown_turn_gives_nothing():
    assert history_window(chat(3), limit=4, before_turn='gone') == []
