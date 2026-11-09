import unittest

import requests

from helpers import user
from x_list_discovery.api import APIError, TwitterAPI, list_id


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self.payload


def client_for(pages):
    calls, queue = [], list(pages)
    def request(url, **kwargs):
        calls.append((url, kwargs['params']))
        return Response(queue.pop(0))
    return TwitterAPI(key='offline-test-key', request=request, sleep=lambda _: None), calls


class APITests(unittest.TestCase):
    def test_duplicate_only_page_advances_to_new_cursor(self):
        first, second = user('Someone', 1), user('Other', 2)
        api, calls = client_for([
            {'followings': [first], 'has_next_page': True, 'next_cursor': 'a'},
            {'followings': [first], 'has_next_page': True, 'next_cursor': 'b'},
            {'followings': [second], 'has_next_page': False}])
        self.assertEqual(len(api.followings('Alice')), 2)
        self.assertEqual(calls[-1][1]['cursor'], 'b')
        self.assertTrue(all(p[1]['pageSize'] == 200 for p in calls))

    def test_missing_repeated_cursor_and_page_limit_are_failures(self):
        for pages, max_pages in [
            ([{'followings': [], 'has_next_page': True}], 5),
            ([{'followings': [], 'has_next_page': True, 'next_cursor': 'same'}] * 2, 5),
            ([{'followings': [], 'has_next_page': True, 'next_cursor': 'a'}], 1),
        ]:
            with self.subTest(pages=pages, limit=max_pages):
                api, _ = client_for(pages)
                with self.assertRaises(APIError):
                    api.followings('Alice', max_pages=max_pages)

    def test_http_200_business_errors_and_wrong_shape_are_not_empty_success(self):
        for data in [{'status': 'error'}, {}, {'followings': [], 'has_next_page': 'false'}]:
            api, _ = client_for([data])
            with self.assertRaises(APIError):
                api.followings('Alice')

    def test_list_urls_and_ids_are_normalized_without_precision_loss(self):
        uid = '1234567890123456789'
        for value in [uid, 'https://x.com/i/lists/' + uid + '?s=20', 'https://twitter.com/i/lists/' + uid]:
            self.assertEqual(list_id(value), uid)
        for value in ['https://evil.example/i/lists/123', 'https://x.com/alice', '../123']:
            with self.assertRaises(ValueError):
                list_id(value)

    def test_list_member_pagination_deduplicates_and_uses_official_shape(self):
        first, second = user('Alice', 1), user('Bob', 2)
        api, calls = client_for([
            {'members': [first], 'has_next_page': True, 'next_cursor': 'a'},
            {'members': [first], 'has_next_page': True, 'next_cursor': 'b'},
            {'members': [second], 'has_next_page': False}])
        self.assertEqual([u['id'] for u in api.list_members('https://x.com/i/lists/123')], ['1', '2'])
        self.assertTrue(all(c[0].endswith('/twitter/list/members') for c in calls))
        self.assertEqual(calls[0][1], {'list_id': '123'})

    def test_incomplete_list_members_never_become_a_complete_snapshot(self):
        for payload in [{'members': [], 'has_next_page': True}, {'data': {'members': []}, 'has_next_page': False}]:
            api, _ = client_for([payload])
            with self.assertRaises(APIError):
                api.list_members('123')

    def test_transient_http_errors_are_retried_without_logging_credentials(self):
        calls, delays = [], []
        def request(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise requests.ConnectionError('sensitive-details-that-must-not-be-printed')
            return Response({'members': [], 'has_next_page': False})
        api = TwitterAPI(key='offline-test-key', request=request, sleep=delays.append)
        self.assertEqual(api.list_members('123'), [])
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.5, 1.0])

    def test_unavailable_user_profile_is_explicit(self):
        api, _ = client_for([{'status': 'success', 'data': {'unavailable': True}}])
        self.assertTrue(api.user('Unavailable')['protected'])
