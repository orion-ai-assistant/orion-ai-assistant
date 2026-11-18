"""Test helper: the old one-tool `add_memory` call shape ({text, type: note|fact|event, ...}) mapped onto the kernel calls the
background memory writer makes (memory.add_note, memory_entities.write_by_model / propose_supersede, memory_events.add_event),
with the old answer shapes, so the older memory tests keep exercising the same behaviour. The chat model itself has only the
`remember` tool now (it queues text); nothing structured is written by a tool any more."""
from __future__ import annotations

import json


class MemoryAddInput(dict):
    """`MemoryAddInput.model_validate({...})` -> the plain dict add_memory below takes."""

    @classmethod
    def model_validate(cls, data):
        return cls(data)

    @classmethod
    def model_validate_json(cls, text):
        return cls(json.loads(text))

    @classmethod
    def model_json_schema(cls):
        return {'properties': {}}


async def add_memory(args: dict, context):
    from orion.kernel import memory
    from orion.kernel.config import get_runtime_settings
    settings = await get_runtime_settings(None, context.user_id)
    kind = args.get('type', 'note')
    if kind == 'fact':
        return await _fact(args, context, settings)
    if kind == 'event':
        return await _event(args, context, settings)
    try:
        added = await memory.add_note(context.user_id, args['text'], settings, source_kind='assistant',
                                      title=args.get('title', ''))
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {'error': str(exc)}
    return {'added': args['text']} if added else {'note': 'This was already in memory.', 'text': args['text']}


async def _fact(args, context, settings):
    from orion.kernel import memory, memory_entities as me
    if not me.enabled(settings):
        return {'error': 'Facts need the user\'s memory collections (the memory_v2 setting), which are off.'}
    if not context.chat_id:
        return {'error': 'A fact needs a source chat; nothing was saved.'}
    text = args['text']
    try:
        category = me.category_of(args.get('category', 'other'))
        recurring = None
        if args.get('repeats') == 'yearly':
            if not args.get('date'):
                return {'error': 'repeats=yearly needs date.'}
            recurring = me.recurring_of(args['date'])
        if args.get('replaces') is not None:
            done = await me.propose_supersede(context.user_id, settings, args['replaces'], text, since=args.get('since') or None,
                                              chat_id=context.chat_id,
                                              category=category if args.get('category', 'other') != 'other' else None,
                                              recurring=recurring)
        else:
            done = await me.write_by_model(context.user_id, settings, context.chat_id, args.get('about', ''),
                                           args.get('about_kind', 'person'), args.get('relation') or None, category, text,
                                           since=args.get('since') or None, recurring=recurring,
                                           add_anyway=args.get('add_anyway', True))
    except me.Ambiguous as exc:
        return {'error': str(exc), 'candidates': exc.candidates}
    except LookupError:
        return {'error': 'There is no current fact with that id.'}
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {'error': str(exc)}
    if 'possible_update' in done:
        return {'status': 'possible_update', 'possible_update': done['possible_update']}
    if 'proposal' in done:
        return {'status': 'awaiting_user_confirmation', 'proposal_id': done['proposal']}
    if 'same_as' in done:
        return {'same_as': done['same_as']}
    fact = done['fact']
    added = {'fact_id': fact['id'], 'about': done['entity']['name'], 'statement': text, 'since': fact['since'],
             'category': fact['category']}
    if fact.get('recurring'):
        added['repeats'] = fact['recurring']
    return {'added': added}


async def _event(args, context, settings):
    from orion.kernel import memory, memory_entities as me, memory_events as ev
    if not ev.enabled(settings):
        return {'error': 'Events need the user\'s memory collections (the memory_v2 setting), which are off.'}
    if not context.chat_id:
        return {'error': 'An event needs a source chat; nothing was saved.'}
    try:
        async with me._db() as conn:
            groups = await ev.groups_of_rows(conn, context.user_id, args.get('items') or []) if args.get('items') else []
        if args.get('event') is not None:
            if not groups:
                return {'error': 'Give items.'}
            done = await ev.set_items(context.user_id, settings, args['event'], add=groups)
            return {'linked': done['linked'], 'event_id': args['event']}
        when = ev.resolve_date(args.get('date') or None, args.get('end_date') or None, args.get('precision') or None,
                               args.get('said_as') or None)
        done = await ev.add_event(context.user_id, settings, args.get('title', ''), when, body=args['text'],
                                  mood=args.get('mood') or None, participants=args.get('participants') or [],
                                  place=args.get('place') or None, items=groups, origin='model',
                                  source={'chat_id': context.chat_id})
    except me.Ambiguous as exc:
        return {'error': str(exc), 'candidates': exc.candidates}
    except LookupError as exc:
        return {'error': str(exc.args[0]) if exc.args and isinstance(exc.args[0], str) else 'No such event.'}
    except (memory.MemoryUnavailable, ValueError) as exc:
        return {'error': str(exc)}
    if 'same_as' in done:
        return {'same_as': done['same_as']}
    event = done['event']
    added = {'event_id': event['id'], 'title': event['title'],
             'date': event['date'] + (f" to {event['end']}" if event.get('end') else ''), 'precision': event['precision']}
    if event.get('said_as'):
        added['said_as'] = event['said_as']
    if event['participants']:
        added['with'] = [p['name'] for p in event['participants']]
    if event['place']:
        added['place'] = event['place']['name']
    if event['items']:
        added['photos'] = len(event['items'])
    result = {'added': added}
    if when.get('note'):
        result['date_note'] = when['note']
    return result
