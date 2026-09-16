"""Targeted field reads and retired-row pagination protect coach correctness."""
from test_api import api


def test_sample_field_projection_keeps_pagination_and_excludes_file_metadata(api):
    client, memory = api
    memory.put('workouts', 'run', {'id': 'run', 'parsed_artifact': {'object': 'samples'}})
    memory.artifacts['samples'] = {'records': [
        {'timestamp': '2026-09-14T18:00:00Z', 'heart_rate': 130, 'distance': 10,
         '_fields': [{'name': 'private_metadata', 'value': 'large'}], 'position_lat': 123},
        {'timestamp': '2026-09-14T18:00:01Z', 'heart_rate': 131, 'distance': 13},
    ]}
    result = client.get('/v1/workouts/run/samples', params={'fields': 'timestamp,heart_rate', 'limit': 1})
    assert result.status_code == 200
    assert result.json()['items'] == [{'timestamp': '2026-09-14T18:00:00Z', 'heart_rate': 130}]
    assert result.json()['next_offset'] == 1 and result.json()['total'] == 2
    assert client.get('/v1/workouts/run/samples', params={'fields': '_fields'}).status_code == 422
    assert client.get('/v1/workouts/run/samples', params={'fields': ''}).status_code == 422


def test_retired_workouts_do_not_break_pagination_or_expose_samples(api):
    client, memory = api
    for identifier, retired in [('a', False), ('b', True), ('c', True), ('d', False), ('e', True)]:
        memory.put('workouts', identifier, {'id': identifier, 'local_date': '2026-09-14',
                   'source_deleted': retired, 'parsed_artifact': {'object': 'unused'}})
    query = {'oldest': '2026-09-14', 'newest': '2026-09-14', 'limit': 1}
    first = client.get('/v1/workouts', params=query).json()
    assert [d['id'] for d in first['items']] == ['a']
    second = client.get('/v1/workouts', params={**query, 'after': first['next_cursor']}).json()
    assert [d['id'] for d in second['items']] == ['d']
    assert second['next_cursor'] is None
    assert client.get('/v1/workouts/b').status_code == 410
    assert client.get('/v1/workouts/b/samples').status_code == 410
    assert memory.get('workouts', 'b')['parsed_artifact'] == {'object': 'unused'}
